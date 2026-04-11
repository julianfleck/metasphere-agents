"""Cron-style job scheduler.

Uses the on-disk schema in ``$METASPHERE_DIR/schedule/jobs.json``.

* **No ``eval``.** Dispatch never shells out to a string; the only
  dispatch path is :func:`dispatch_to_agent`, which uses ``subprocess.run``
  with an explicit argv.
* **File locking on every read-modify-write.** ``load_jobs`` /
  ``save_jobs`` go through :func:`metasphere.io.file_lock` +
  :func:`metasphere.io.write_json` (atomic tmp+rename + flock).
* **Shrink-detection guard.** ``save_jobs`` refuses to write zero jobs
  when the input had jobs — protects against the subshell-pipe wipe bug
  that previously truncated ``jobs.json`` to ``[]``.
* **180s fire window.** Protects against tick drift, restarts,
  briefly-paused daemons.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import logging
import subprocess
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

try:
    from croniter import croniter
except ImportError as e:  # pragma: no cover
    raise ImportError(
        "metasphere.schedule requires the 'croniter' package."
    ) from e

from . import agents as _agents
from .events import log_event
from .io import atomic_write_text, file_lock
from .messages import send_message
from .paths import Paths, resolve

logger = logging.getLogger(__name__)


# ---------- schema ----------

@dataclass
class Job:
    """A scheduled job. Mirrors jobs.json field-for-field."""

    id: str
    source: str = ""
    source_id: str = ""
    agent_id: str = "main"
    name: str = ""
    enabled: bool = True
    kind: str = "cron"
    cron_expr: str = ""
    tz: str = "UTC"
    payload_kind: str = "agentTurn"
    payload_message: str = ""
    model: str = ""
    session_target: str = "isolated"
    wake_mode: str = "next-heartbeat"
    imported_at: int = 0
    last_fired_at: int = 0
    next_run: int = 0
    command: str = ""
    full_command: str = ""

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Job":
        known = {f.name for f in dataclasses.fields(cls)}
        return cls(**{k: v for k, v in d.items() if k in known})


@dataclass
class FireResult:
    job_id: str
    name: str
    target_agent: str
    fired: bool
    dispatched: bool
    error: str = ""


# ---------- load / save ----------

def _read_jobs_unlocked(jobs_path: Path) -> list[Job]:
    if not jobs_path.exists():
        return []
    try:
        raw = json.loads(jobs_path.read_text(encoding="utf-8") or "[]")
    except (OSError, json.JSONDecodeError):
        return []
    if not isinstance(raw, list):
        return []
    return [Job.from_dict(j) for j in raw if isinstance(j, dict)]


def _write_jobs_unlocked(jobs_path: Path, jobs: list[Job], *, input_count: int) -> None:
    """Write jobs.json without acquiring a lock. Caller must hold one.

    Honors the shrink-detection guard.
    """
    if input_count > 0 and len(jobs) == 0:
        raise RuntimeError(
            f"refusing to wipe jobs.json: input had {input_count} jobs, output has 0"
        )
    payload = json.dumps([j.to_dict() for j in jobs], indent=2, sort_keys=True) + "\n"
    atomic_write_text(jobs_path, payload)


def load_jobs(paths: Paths | None = None) -> list[Job]:
    """Snapshot read of jobs.json under a shared lock."""
    paths = paths or resolve()
    jobs_path = paths.schedule_jobs
    with file_lock(jobs_path, exclusive=False):
        return _read_jobs_unlocked(jobs_path)


@contextmanager
def with_locked_jobs(paths: Paths | None = None) -> Iterator[list[Job]]:
    """Hold a single exclusive lock for the entire load→mutate→save cycle.

    Yields the current jobs list. Callers commit by calling
    :func:`save_jobs` *inside* the block — that path skips relocking and
    uses the surrounding lock as the only critical section.
    """
    paths = paths or resolve()
    jobs_path = paths.schedule_jobs
    with file_lock(jobs_path):
        yield _read_jobs_unlocked(jobs_path)


def save_jobs(jobs: list[Job], paths: Paths | None = None, *, _input_count: int) -> None:
    """Write jobs.json. Refuses to wipe if ``_input_count`` > 0 and ``jobs`` is empty.

    ``_input_count`` is mandatory: callers compute it under the lock they
    hold around the load, eliminating the TOCTOU window.
    Must be called from within a :func:`with_locked_jobs` block (or the
    caller must otherwise hold the schedule_jobs flock).
    """
    paths = paths or resolve()
    _write_jobs_unlocked(paths.schedule_jobs, jobs, input_count=_input_count)


# ---------- cron evaluation ----------

# 180s window. Wide enough to survive a missed
# tick from a restart/pause, narrow enough to not double-fire on the next
# minute.
CRON_WINDOW_SECS = 180


def cron_should_fire(
    expr: str,
    tz: str,
    last_fired_at: int,
    now: int | None = None,
) -> bool:
    """Return True if the cron expression is due to fire right now.

    Uses croniter for cron parsing. Honors timezone via
    zoneinfo so weekday/hour calculations are local-time correct.

    Fires when the most recent expected fire time is within the last
    ``CRON_WINDOW_SECS`` seconds AND we have not already fired since then
    (``prev_epoch > last_fired_at``).
    """
    try:
        from zoneinfo import ZoneInfo
    except ImportError:  # pragma: no cover - py < 3.9
        ZoneInfo = None  # type: ignore

    if not expr:
        return False

    now = int(now if now is not None else time.time())

    try:
        zone = ZoneInfo(tz) if ZoneInfo else None
    except Exception:
        zone = ZoneInfo("UTC") if ZoneInfo else None

    now_dt = _dt.datetime.fromtimestamp(now, tz=zone) if zone else _dt.datetime.fromtimestamp(now)
    try:
        itr = croniter(expr, now_dt)
        prev = itr.get_prev(_dt.datetime)
    except Exception:
        return False

    prev_epoch = int(prev.timestamp())
    delta = now - prev_epoch
    return 0 <= delta < CRON_WINDOW_SECS and prev_epoch > int(last_fired_at or 0)


# ---------- target resolution ----------

def resolve_target_agent(job: Job) -> str:
    """Map a job to its persistent collaborator agent.

    Map a job to its persistent collaborator agent by name prefix
    (``@briefing``, ``@polymarket``, ``@research-*``, ``@explorer``,
    ``@rage-changelog``, etc.).
    """
    name = job.name or ""
    if name.startswith("research-monitor:"):
        # Project-scoped: research agents live in projects/research/agents/
        return "@" + name[len("research-monitor:"):]
    if name.startswith("polymarket:"):
        return "@polymarket"
    if name.startswith("spot:autonomous-exploration"):
        return "@explorer"
    if name.startswith("rage-changelog"):
        return "@rage-changelog"
    if name.startswith("Morning briefing"):
        return "@briefing"
    return "@" + (job.agent_id or "main")


# ---------- dispatch ----------

def _wake_script(paths: Paths) -> Path:
    return paths.project_root / "scripts" / "metasphere-wake"


def _find_mission(target_agent: str, paths: Paths) -> Path | None:
    """Return the ``MISSION.md`` path for ``target_agent`` if it names a
    persistent agent (global or project-scoped), else None.

    Mirrors :func:`metasphere.agents._find_agent_dir` precedence —
    project-scoped dirs first, then global. We only need the existence
    check, not a full :class:`AgentRecord`.
    """
    if paths.projects.is_dir():
        try:
            for proj_dir in sorted(paths.projects.iterdir()):
                if not proj_dir.is_dir():
                    continue
                mission = proj_dir / "agents" / target_agent / "MISSION.md"
                if mission.is_file():
                    return mission
        except OSError:
            pass
    mission = paths.agent_dir(target_agent) / "MISSION.md"
    if mission.is_file():
        return mission
    return None


def _wake_target(
    target_agent: str,
    first_task: str | None,
    paths: Paths,
) -> bool:
    """Wake ``target_agent`` via :func:`metasphere.agents.wake_persistent`.

    Idempotent: if the tmux session is already alive, the helper just
    injects ``first_task`` (if any) and returns. Returns True on success,
    False on any exception — callers fall back to inbox-only delivery.
    """
    try:
        _agents.wake_persistent(
            target_agent, first_task=first_task, paths=paths,
        )
        return True
    except Exception as e:
        logger.warning("wake_persistent failed for %s: %s", target_agent, e)
        return False


def _extract_messages_send_target(payload: str) -> str | None:
    """Parse ``payload`` as a ``messages send @X !label ...`` command and
    return ``@X`` if it matches, else None.

    Handles both bare ``messages`` (assumed on PATH) and full-path forms
    like ``/usr/local/bin/messages`` or ``scripts/messages``.
    """
    import shlex

    try:
        argv = shlex.split(payload or "")
    except ValueError:
        return None
    if len(argv) < 4:
        return None
    for i in range(len(argv) - 3):
        if Path(argv[i]).name != "messages":
            continue
        if argv[i + 1] != "send":
            continue
        tgt = argv[i + 2]
        if tgt.startswith("@"):
            return tgt
        return None
    return None


def dispatch_command(
    payload: str,
    *,
    paths: Paths | None = None,
    timeout: int = 600,
) -> bool:
    """Execute a ``payload_kind=="command"`` job.

    Splits ``payload`` with :func:`shlex.split` (no shell, no eval) and
    runs the resulting argv via :func:`subprocess.run`. Returns True on
    exit-code 0.

    Pre-wake: if ``payload`` is a ``messages send @X !task ...`` command
    and ``@X`` is a persistent agent (has ``MISSION.md`` under global or
    project agents), we first wake ``@X``'s tmux+REPL via
    :func:`metasphere.agents.wake_persistent`. Without this, the
    subsequent ``messages send`` writes the inbox file but
    ``wake_recipient_if_live`` silently no-ops on a dormant session, so
    scheduled polymarket / research tasks accumulate unread forever.
    """
    import shlex

    if not payload:
        return False
    try:
        argv = shlex.split(payload)
    except ValueError as e:
        logger.warning("dispatch_command: bad payload %r: %s", payload, e)
        return False
    if not argv:
        return False

    paths = paths or resolve()
    target = _extract_messages_send_target(payload)
    if target is not None and _find_mission(target, paths) is not None:
        _wake_target(target, first_task=None, paths=paths)

    try:
        proc = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            logger.warning(
                "dispatch_command: %s exited %d: %s",
                argv[0], proc.returncode, (proc.stderr or "").strip()[:200],
            )
        return proc.returncode == 0
    except Exception as e:  # pragma: no cover - defensive
        logger.warning("dispatch_command failed for %r: %s", argv, e)
        return False


def dispatch_to_agent(
    target_agent: str,
    payload: str,
    *,
    paths: Paths | None = None,
    job_name: str = "",
) -> bool:
    """Wake the target agent or fall back to a ``!task`` message.

    If the agent has a ``MISSION.md`` (global **or** project-scoped) we
    treat it as a persistent collaborator and call
    :func:`metasphere.agents.wake_persistent` — this starts the tmux+REPL
    session if dormant and injects ``payload`` as a first task. Otherwise
    we drop a ``!task`` message into its inbox via
    :func:`metasphere.messages.send_message`.
    """
    paths = paths or resolve()

    if _find_mission(target_agent, paths) is not None:
        if _wake_target(target_agent, first_task=payload, paths=paths):
            return True
        # Fall through to inbox-only delivery if wake itself failed.

    try:
        send_message(
            target_agent,
            "!task",
            payload or job_name or "scheduled task",
            from_agent="@scheduler",
            paths=paths,
            wake=False,
        )
        return True
    except Exception as e:
        logger.warning("send_message fallback failed for %s: %s", target_agent, e)
        return False


# ---------- run ----------

def run_due_jobs(paths: Paths | None = None, *, now: int | None = None) -> list[FireResult]:
    """Iterate jobs, fire those that are due, persist last_fired_at.

    Single read-modify-write under the load_jobs/save_jobs lock cycle.
    Disabled jobs and non-cron jobs are preserved untouched.
    """
    paths = paths or resolve()
    now = int(now if now is not None else time.time())
    results: list[FireResult] = []

    with with_locked_jobs(paths) as jobs:
        input_count = len(jobs)
        for job in jobs:
            if not job.enabled or job.kind != "cron":
                continue
            if not cron_should_fire(job.cron_expr, job.tz, job.last_fired_at, now=now):
                continue

            target = resolve_target_agent(job)
            job.last_fired_at = now

            try:
                log_event(
                    "schedule.cron_fire",
                    job.name or job.id,
                    agent=target,
                    meta={"job_id": job.id, "cron_expr": job.cron_expr, "tz": job.tz},
                    paths=paths,
                )
            except Exception as e:  # pragma: no cover - defensive
                logger.warning("log_event failed: %s", e)

            if job.payload_kind == "command":
                ok = dispatch_command(job.payload_message, paths=paths)
            else:
                ok = dispatch_to_agent(
                    target,
                    job.payload_message,
                    paths=paths,
                    job_name=job.name,
                )
            results.append(
                FireResult(
                    job_id=job.id,
                    name=job.name,
                    target_agent=target,
                    fired=True,
                    dispatched=ok,
                    error="" if ok else "dispatch failed",
                )
            )

        if results:
            save_jobs(jobs, paths, _input_count=input_count)

    return results


# ---------- list / enable / disable ----------

def list_jobs(paths: Paths | None = None) -> list[Job]:
    return load_jobs(paths)


def set_enabled(job_id: str, enabled: bool, paths: Paths | None = None) -> bool:
    paths = paths or resolve()
    with with_locked_jobs(paths) as jobs:
        input_count = len(jobs)
        found = False
        for j in jobs:
            if j.id == job_id:
                j.enabled = enabled
                found = True
                break
        if found:
            save_jobs(jobs, paths, _input_count=input_count)
    return found
