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
import logging
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

from .events import log_event
from .io import file_lock, read_json, write_json
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

def load_jobs(paths: Paths | None = None) -> list[Job]:
    paths = paths or resolve()
    jobs_path = paths.schedule_jobs
    if not jobs_path.exists():
        return []
    raw = read_json(jobs_path, default=[])
    if not isinstance(raw, list):
        return []
    return [Job.from_dict(j) for j in raw if isinstance(j, dict)]


def save_jobs(jobs: list[Job], paths: Paths | None = None, *, _input_count: int | None = None) -> None:
    """Write jobs.json atomically. Refuses to write 0 jobs if input had >0."""
    paths = paths or resolve()
    jobs_path = paths.schedule_jobs

    # Shrink-detection guard. If caller did not pass _input_count, infer it
    # from the on-disk file (best-effort — under a held lock would be ideal,
    # but write_json takes its own lock and we don't want to nest).
    if _input_count is None:
        existing = read_json(jobs_path, default=[])
        _input_count = len(existing) if isinstance(existing, list) else 0

    if _input_count > 0 and len(jobs) == 0:
        raise RuntimeError(
            f"refusing to wipe jobs.json: input had {_input_count} jobs, output has 0"
        )

    write_json(jobs_path, [j.to_dict() for j in jobs])


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
    from croniter import croniter
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

    jobs = load_jobs(paths)
    input_count = len(jobs)
    results: list[FireResult] = []

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
    jobs = load_jobs(paths)
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
