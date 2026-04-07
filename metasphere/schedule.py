"""Cron-style job scheduler.

Port of ``scripts/metasphere-schedule``. Mirrors the on-disk schema in
``$METASPHERE_DIR/schedule/jobs.json`` exactly so the bash and Python
implementations can coexist during the rewrite.

Security/correctness deltas vs the bash version:

* **No ``eval``.** The bash ``cmd_run`` ``eval``-s ``full_command`` from
  disk (PORTING risk #2). Here we never shell out to a string; the only
  dispatch path is :func:`dispatch_to_agent`, which uses ``subprocess.run``
  with an explicit argv.
* **File locking on every read-modify-write.** ``load_jobs`` /
  ``save_jobs`` go through :func:`metasphere.io.file_lock` +
  :func:`metasphere.io.write_json` (atomic tmp+rename + flock).
* **Shrink-detection guard.** ``save_jobs`` refuses to write zero jobs
  when the input had jobs — protects against the subshell-pipe wipe bug
  that previously truncated ``jobs.json`` to ``[]``.
* **180s fire window.** Same as the bash patch — protects against tick
  drift, restarts, briefly-paused daemons.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import logging
import os
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
        "metasphere.schedule requires the 'croniter' package "
        "(see PORTING.md — stdlib + croniter only)."
    ) from e

from .events import log_event
from .io import atomic_write_text, file_lock, write_json
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
    hold around the load, eliminating the TOCTOU window M1/M2 flagged.
    Must be called from within a :func:`with_locked_jobs` block (or the
    caller must otherwise hold the schedule_jobs flock).
    """
    paths = paths or resolve()
    _write_jobs_unlocked(paths.schedule_jobs, jobs, input_count=_input_count)


# ---------- cron evaluation ----------

# 180s window — same as the bash patch. Wide enough to survive a missed
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

    Uses croniter (system-wide install — see PORTING). Honors timezone via
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

    Mirrors the bash ``case "$name"`` block — the openclaw migration left
    every job's ``agent_id`` as ``main``, but mission-writer split them
    into named persistent agents (``@briefing``, ``@polymarket``,
    ``@research-*``, ``@explorer``, ``@rage-changelog``) by name prefix.
    """
    name = job.name or ""
    if name.startswith("research-monitor:"):
        return "@research-" + name[len("research-monitor:"):]
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
    return paths.repo / "scripts" / "metasphere-wake"


def dispatch_to_agent(
    target_agent: str,
    payload: str,
    *,
    paths: Paths | None = None,
    job_name: str = "",
) -> bool:
    """Wake the target agent or fall back to a ``!task`` message.

    If the agent has a ``MISSION.md`` we treat it as a persistent
    collaborator and call ``scripts/metasphere-wake`` (subprocess, explicit
    argv — never via shell). Otherwise we drop a ``!task`` message into
    its inbox via :func:`metasphere.messages.send_message`.
    """
    paths = paths or resolve()
    mission = paths.agent_dir(target_agent) / "MISSION.md"

    if mission.exists():
        wake = _wake_script(paths)
        if wake.exists():
            try:
                subprocess.run(
                    [str(wake), target_agent, payload],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )
                return True
            except Exception as e:  # pragma: no cover - defensive
                logger.warning("metasphere-wake failed for %s: %s", target_agent, e)
                return False

    # Fallback: drop a task message into the inbox.
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
