"""Task lifecycle consolidation.

This module runs on a short cron (every ~5 minutes) and classifies every
active task into one of six lifecycle verdicts:

* **ACTIVE**    — ``updated_at`` (or ``last_pinged_at``, used as a cooldown
                  marker) is within the stale window. Leave alone.
* **STALE**     — assigned, but both ``updated_at`` and ``last_pinged_at``
                  are older than the stale window. Ping the owning agent
                  for a status check; escalate to @orchestrator or @user
                  if the ping count crosses the threshold.
* **BLOCKED**   — ``status`` starts with ``blocked``. Waiting on something
                  external — don't ping.
* **UNOWNED**   — ``assigned_to`` is empty and no recent activity. Escalate
                  to @orchestrator for triage.
* **ABANDONED** — UNOWNED, pinged out, AND ``created_at`` older than the
                  abandon window. Terminal: archive to
                  ``.tasks/archive/_abandoned/`` so the task stops cycling
                  through @orchestrator forever.
* **DONE**      — ``status`` starts with ``complete`` but the file is still
                  in ``active/``. Archive immediately.

The git-commit collector from the previous incarnation of this module is
kept as ONE optional signal: if a commit in the recent window references
the task slug verbatim, the task's effective ``updated_at`` is bumped to
the commit time before classification. This closes the loop for code
work without a separate code path — most tasks won't have any commit
evidence and that's fine.

Safety: the only mutating actions are
:func:`metasphere.tasks.add_update` (appends a note / bumps updated_at),
:func:`metasphere.tasks.update_task` (writes ``last_pinged_at`` +
``ping_count``), :func:`metasphere.tasks.complete_task` (archive), and
:func:`metasphere.messages.send_message` (ping/escalate). No silent
deletions.
"""

from __future__ import annotations

import datetime as _dt
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from . import messages as _messages
from . import schedule as _sched
from . import tasks as _tasks
from .events import log_event
from .paths import Paths, resolve

# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------

VERDICT_ACTIVE = "ACTIVE"
VERDICT_STALE = "STALE"
VERDICT_BLOCKED = "BLOCKED"
VERDICT_PAUSED = "PAUSED"
VERDICT_UNOWNED = "UNOWNED"
VERDICT_ABANDONED = "ABANDONED"
VERDICT_DONE = "DONE"

VERDICTS = (
    VERDICT_ACTIVE,
    VERDICT_STALE,
    VERDICT_BLOCKED,
    VERDICT_PAUSED,
    VERDICT_UNOWNED,
    VERDICT_ABANDONED,
    VERDICT_DONE,
)

# Message-lifecycle verdicts. Parallel to task verdicts but distinct so
# the two scans don't stomp on each other's rendering.
MSG_VERDICT_ACTIVE = "MSG-ACTIVE"
MSG_VERDICT_STALE = "MSG-STALE"
MSG_VERDICT_UNREAD_OLD = "MSG-UNREAD-OLD"
MSG_VERDICT_DONE_PENDING_ARCHIVE = "MSG-DONE-PENDING-ARCHIVE"
MSG_VERDICT_INFO_AUTO_ARCHIVE = "MSG-INFO-AUTO-ARCHIVE"
#: ``!done`` notification messages that have aged past the auto-archive
#: window. Terminal — handler archives them without requiring read_at.
#: Fixes the 2026-04-15 self-audit gap where every ``msg done`` spawned
#: a new ``!done`` notification that never entered terminal state and
#: got stale-pinged forever.
MSG_VERDICT_DONE = "MSG-DONE"
MSG_VERDICT_PINNED = "MSG-PINNED"  # !task/!query — pinned until explicitly completed

MSG_VERDICTS = (
    MSG_VERDICT_ACTIVE,
    MSG_VERDICT_STALE,
    MSG_VERDICT_UNREAD_OLD,
    MSG_VERDICT_DONE_PENDING_ARCHIVE,
    MSG_VERDICT_INFO_AUTO_ARCHIVE,
    MSG_VERDICT_DONE,
    MSG_VERDICT_PINNED,
)

# Info messages are auto-archived once they've been read for more than
# this long. They're just notifications; nothing acts on them.
INFO_AUTO_ARCHIVE_AFTER_MINUTES = 60

# Built-in system agents that are virtual — no agent_dir on disk
# anywhere — and therefore have no human/REPL reader behind them.
# Used as a fast-path for `_is_no_reader` and as a fallback when no
# Paths object is available (e.g. in unit tests that don't construct
# a tmp_paths fixture). The agent_dir-existence check in
# `_is_no_reader` catches every other no-reader case (GC'd ephemerals,
# any future virtual agent) without needing to be added here.
SYSTEM_AGENTS_NO_READER = frozenset({
    "@consolidate",
    "@scheduler",
    "@daemon-supervisor",
    "@supervisor",
})


def _is_no_reader(agent_name: str, paths: "Paths | None" = None) -> bool:
    """True if a message addressed to this agent has no reader behind it.

    Three classes are caught:
    - Built-in virtual system agents in `SYSTEM_AGENTS_NO_READER` (fast
      path; also covers paths-less test contexts).
    - GC'd ephemeral agents whose agent_dir was rmtree'd on cleanup.
    - Any other agent with no global or project-scoped agent_dir on disk.

    Pinging a no-reader recipient as STALE spawns another no-reader
    message that itself ages into STALE — a self-sustaining loop. The
    consolidator should auto-archive instead.
    """
    if not agent_name:
        return False
    from . import agents as _agents
    name = _agents._normalize_name(agent_name)
    if name in SYSTEM_AGENTS_NO_READER:
        return True
    if paths is None:
        return False
    if paths.agent_dir(name).exists():
        return False
    if paths.projects.exists():
        for proj in paths.projects.iterdir():
            if (proj / "agents" / name).exists():
                return False
    return True

# Default lifecycle window. Anything not touched within this many minutes
# is a candidate for a status-check ping.
STALE_WINDOW_MINUTES_DEFAULT = 15

# After this many pings without progress, escalate a step further
# (orchestrator → user).
PING_ESCALATE_THRESHOLD_DEFAULT = 3

# An UNOWNED task that has been pinged out AND is older than this many
# days is considered ABANDONED — archive it instead of leaving it to
# noop-bounce against @orchestrator forever. Tunable; tasks newer than
# this stay in the noop-pinged-out state until they age in.
ABANDONED_AGE_DAYS_DEFAULT = 3

# Git lookback window. Only used as a soft signal that bumps updated_at.
DEFAULT_SINCE = "2d"


# ---------------------------------------------------------------------------
# Schedule integration
# ---------------------------------------------------------------------------

JOB_ID = "metasphere-task-consolidate"
JOB_NAME = "task:consolidate"
JOB_CRON = "*/5 * * * *"  # every 5 minutes


def build_job() -> _sched.Job:
    return _sched.Job(
        id=JOB_ID,
        source="consolidate",
        source_id=JOB_ID,
        agent_id="consolidate",
        name=JOB_NAME,
        enabled=True,
        kind="cron",
        cron_expr=JOB_CRON,
        tz="UTC",
        payload_kind="command",
        payload_message=f"{sys.executable} -m metasphere.cli.main consolidate run",
        command=f"{sys.executable} -m metasphere.cli.main consolidate run",
        full_command=f"{sys.executable} -m metasphere.cli.main consolidate run",
    )


def register_job(paths: Paths | None = None) -> _sched.Job:
    paths = paths or resolve()
    paths.schedule.mkdir(parents=True, exist_ok=True)
    new_job = build_job()
    with _sched.with_locked_jobs(paths) as jobs:
        input_count = len(jobs)
        replaced = False
        for i, j in enumerate(jobs):
            if j.id == JOB_ID:
                new_job.last_fired_at = j.last_fired_at
                jobs[i] = new_job
                replaced = True
                break
        if not replaced:
            jobs.append(new_job)
        _sched.save_jobs(jobs, paths, _input_count=input_count)
    return new_job


def unregister_job(paths: Paths | None = None) -> bool:
    paths = paths or resolve()
    if not paths.schedule_jobs.exists():
        return False
    with _sched.with_locked_jobs(paths) as jobs:
        input_count = len(jobs)
        kept = [j for j in jobs if j.id != JOB_ID]
        if len(kept) == input_count:
            return False
        if not kept and input_count > 0:
            paths.schedule_jobs.write_text("[]\n", encoding="utf-8")
            return True
        _sched.save_jobs(kept, paths, _input_count=input_count)
    return True


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


def scan_active_tasks() -> list[_tasks.Task]:
    """Return every task currently in any canonical ``.tasks/active/``.

    Walks ``~/.metasphere/projects/*/.tasks/`` and ``~/.metasphere/tasks/``
    (see ``tasks._canonical_tasks_dirs``).
    """
    out: list[_tasks.Task] = []
    for tasks_dir in _tasks._canonical_tasks_dirs():
        active = tasks_dir / "active"
        if not active.is_dir():
            continue
        for f in sorted(active.glob("*.md")):
            try:
                out.append(_tasks.Task.from_text(f.read_text(encoding="utf-8"), path=f))
            except Exception:
                continue
    return out


# ---------------------------------------------------------------------------
# Git-commit soft signal
# ---------------------------------------------------------------------------

_SLUG_RE_CACHE: dict[str, re.Pattern[str]] = {}


def _slug_pattern(slug: str) -> re.Pattern[str]:
    p = _SLUG_RE_CACHE.get(slug)
    if p is None:
        p = re.compile(r"(?<![\w-])" + re.escape(slug) + r"(?![\w-])", re.IGNORECASE)
        _SLUG_RE_CACHE[slug] = p
    return p


_SINCE_SHORTHAND = re.compile(r"^(\d+)\s*([dwhm])$")


def _normalize_since(since: str) -> str:
    m = _SINCE_SHORTHAND.match(since.strip())
    if not m:
        return since
    n, unit = m.group(1), m.group(2)
    word = {"d": "days", "w": "weeks", "h": "hours", "m": "minutes"}[unit]
    return f"{n} {word} ago"


def _git_log(project_root: Path, since: str) -> list[tuple[str, str, str, str]]:
    """Return ``[(sha, iso_date, subject, body)]`` for commits in the window."""
    sep = "\x1e"
    fmt = f"%H%x09%cI%x09%s%x09%b{sep}"
    try:
        out = subprocess.check_output(
            ["git", "-C", str(project_root), "log",
             f"--since={_normalize_since(since)}", f"--pretty=format:{fmt}"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    records: list[tuple[str, str, str, str]] = []
    for chunk in out.split(sep):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        parts = chunk.split("\t", 3)
        if len(parts) < 3:
            continue
        sha = parts[0]
        iso = parts[1]
        subject = parts[2]
        body = parts[3] if len(parts) > 3 else ""
        records.append((sha, iso, subject, body))
    return records


def _commit_touches(
    task: _tasks.Task, commits: list[tuple[str, str, str, str]]
) -> tuple[str, str] | None:
    """If any commit references the task slug, return (sha, iso_date) of the newest."""
    slug = task.id
    if not slug:
        return None
    pat = _slug_pattern(slug)
    best: tuple[str, str] | None = None
    for sha, iso, subject, body in commits:
        if pat.search(f"{subject}\n{body}"):
            if best is None or iso > best[1]:
                best = (sha[:12], iso)
    return best


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _utcnow() -> _dt.datetime:
    return _dt.datetime.now(_dt.timezone.utc)


def _parse_iso(s: str) -> _dt.datetime | None:
    if not s:
        return None
    try:
        # Accept trailing Z and offset forms alike.
        v = s.replace("Z", "+00:00")
        dt = _dt.datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return dt
    except ValueError:
        return None


def classify_task(
    task: _tasks.Task,
    *,
    now: _dt.datetime | None = None,
    stale_window_minutes: int = STALE_WINDOW_MINUTES_DEFAULT,
    ping_escalate_threshold: int = PING_ESCALATE_THRESHOLD_DEFAULT,
    abandoned_age_days: int = ABANDONED_AGE_DAYS_DEFAULT,
    paths: Paths | None = None,
) -> str:
    """Return one of the lifecycle verdicts for ``task``.

    When ``paths`` is supplied, an assignee that names an agent whose
    directory no longer exists (a GC'd ephemeral) is treated as orphan
    and routed through the UNOWNED branch — same abandon-after-ping-out
    behaviour as ``@unassigned``. Without ``paths`` the orphan check is
    skipped (existing tests run with no Paths and must keep STALE
    semantics for named assignees).
    """
    now = now or _utcnow()
    window = _dt.timedelta(minutes=stale_window_minutes)

    status = (task.status or "").strip().lower()
    if status.startswith("complete"):
        return VERDICT_DONE
    if status.startswith("blocked"):
        return VERDICT_BLOCKED
    # PAUSED is a terminal-ish state: the owner has deliberately put
    # the task on hold, and the consolidator should stop pinging until
    # the status is manually changed. Must be checked BEFORE the stale
    # window so a paused task doesn't get re-escalated every cycle
    # (the bug Julian flagged 2026-04-15T08:55Z that drove 8
    # STALE→escalated-user events per 15-min cycle on his worldwire
    # tasks).
    if status.startswith("paused"):
        return VERDICT_PAUSED

    updated = _parse_iso(task.updated)
    if updated and (now - updated) < window:
        return VERDICT_ACTIVE

    # Cooldown: if we recently pinged, don't re-ping even though
    # updated_at is stale. Treat as ACTIVE for this cycle.
    last_ping = _parse_iso(task.last_pinged_at)
    if last_ping and (now - last_ping) < window:
        return VERDICT_ACTIVE

    # "@unassigned" is a sentinel the CLI writes when `metasphere task new`
    # is called without an owner — it is semantically equivalent to an
    # empty assignee (see cli/tasks.py:248 which treats them identically
    # for the --unassigned filter). Classify it through the UNOWNED path
    # so it goes quiet after ping_escalate_threshold instead of firing
    # STALE→escalate_to_user every cooldown cycle.
    #
    # Same for tasks assigned to a GC'd ephemeral whose agent dir no
    # longer exists anywhere — pinging a vanished assignee accomplishes
    # nothing, escalating to orchestrator forever fills the inbox.
    # Verified live 2026-04-25T10:00Z: 25 worldwire-orphan tasks at
    # ping_count 280-294, age 4d, all assigned to @ww-* ephemerals
    # whose dirs were rmtree'd by the standard ephemeral GC.
    is_orphan_assignee = (
        paths is not None
        and task.assignee
        and task.assignee != "@unassigned"
        and not _agent_exists_anywhere(task.assignee, paths)
    )
    if not task.assignee or task.assignee == "@unassigned" or is_orphan_assignee:
        # Terminal ABANDONED: orphan task that has already been pinged
        # out AND is older than the abandon window. Without this branch,
        # the task ping-bounces forever between UNOWNED → noop-pinged-out
        # every cooldown cycle and never leaves active/.
        created = _parse_iso(task.created)
        abandon_window = _dt.timedelta(days=abandoned_age_days)
        if (
            task.ping_count >= ping_escalate_threshold
            and created is not None
            and (now - created) >= abandon_window
        ):
            return VERDICT_ABANDONED
        return VERDICT_UNOWNED
    return VERDICT_STALE


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


def _is_persistent_agent(agent_id: str, paths: Paths) -> bool:
    if not agent_id:
        return False
    name = agent_id if agent_id.startswith("@") else "@" + agent_id
    agent_dir = paths.agent_dir(name)
    # Either marker is sufficient. Bootstrap writes persona-index.md,
    # SOUL.md, and MISSION.md in sequence, so a mid-bootstrap scope dir
    # may have only persona-index.md. Treating either as a persistence
    # signal closes the GC race that reaped 9 newly-created personas
    # on 2026-04-14.
    return (agent_dir / "MISSION.md").exists() or (agent_dir / "persona-index.md").exists()


def _agent_exists_anywhere(agent_id: str, paths: Paths) -> bool:
    """True if the agent dir exists in global agents/ or any project agents/.

    Distinguishes a GC'd ephemeral (returns False — task is orphan) from
    a live ephemeral (dir still present with status/task/etc) or a
    persistent agent. Ephemerals can live either at the global root
    (~/.metasphere/agents/@x/) or under a project (e.g.
    ~/.metasphere/projects/<proj>/agents/@x/, where @explorer lives),
    so both locations must be checked.
    """
    if not agent_id or agent_id == "@unassigned":
        return False
    name = agent_id if agent_id.startswith("@") else "@" + agent_id
    if paths.agent_dir(name).exists():
        return True
    projects_root = paths.projects
    if not projects_root.exists():
        return False
    try:
        for project_dir in projects_root.iterdir():
            if project_dir.is_dir() and (project_dir / "agents" / name).exists():
                return True
    except OSError:
        pass
    return False


def _bump_ping(task: _tasks.Task, project_root: Path) -> _tasks.Task:
    """Write ``last_pinged_at`` (now) and increment ``ping_count``."""
    now_iso = _utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    return _tasks.update_task(
        task.id,
        project_root,
        last_pinged_at=now_iso,
        ping_count=task.ping_count + 1,
    )


def _last_update_line(body: str) -> str:
    """Extract the most recent ``- <ts> <note>`` line under ``## Updates``."""
    if "## Updates" not in body:
        return ""
    section = body.split("## Updates", 1)[1]
    lines = [l.strip() for l in section.splitlines() if l.strip().startswith("- ")]
    return lines[-1].lstrip("- ").strip() if lines else ""


def _route_ping_target(task: _tasks.Task, paths: Paths) -> str:
    """Resolve the preferred recipient for a stale-task ``!query``.

    Per Julian 2026-04-15T08:55Z: route to the project's lead first so
    external collaborators don't spam Julian's view with pings for
    worldwire tasks he doesn't own. Falls back to the task's
    ``assigned_to`` only when the project has no lead (or no
    project at all).

    Order:
      1. ``@<project>-lead`` if a member with that literal id exists
      2. first member with role == "lead"
      3. ``task.assignee`` (pre-PR #11 behavior)
    """
    if not task.project:
        return task.assignee
    try:
        from .project import Project
        proj = Project.for_name(task.project, paths)
    except Exception:
        return task.assignee
    if proj is None:
        return task.assignee
    lead_id = f"@{task.project}-lead"
    for m in proj.members:
        if m.id == lead_id:
            return m.id
    for m in proj.members:
        if getattr(m, "role", "") == "lead":
            return m.id
    return task.assignee


def ping_persistent_agent(
    task: _tasks.Task,
    project_root: Path,
    paths: Paths,
    *,
    sender: Callable[..., object] | None = None,
) -> dict:
    """Send a ``!query`` status-check.

    Routes to the project's lead when one is registered (see
    :func:`_route_ping_target`), otherwise the task's assignee.
    """
    sender = sender or _default_sender()
    target = _route_ping_target(task, paths)
    body = (
        f"status check on {task.id}: still working, done, blocked, or paused?\n"
        f"title: {task.title}\n"
        f"last update: {_last_update_line(task.body) or '(none)'}"
    )
    try:
        sender(target, "!query", body, "@consolidate", paths=paths)
        delivered = True
    except Exception as e:  # pragma: no cover - defensive
        delivered = False
        body = f"error: {e}"
    _bump_ping(task, project_root)
    return {"action": "pinged", "target": target, "delivered": delivered}


def escalate_to_orchestrator(
    task: _tasks.Task,
    reason: str,
    project_root: Path,
    paths: Paths,
    *,
    sender: Callable[..., object] | None = None,
) -> dict:
    sender = sender or _default_sender()
    body = (
        f"stale task review: {task.id} ({reason}) — "
        f"original: {task.title}, "
        f"last update: {_last_update_line(task.body) or '(none)'}, "
        f"ping_count={task.ping_count}"
    )
    try:
        sender("@orchestrator", "!info", body, "@consolidate", paths=paths)
        delivered = True
    except Exception as e:  # pragma: no cover - defensive
        delivered = False
        body = f"error: {e}"
    _bump_ping(task, project_root)
    return {"action": "escalated-orchestrator", "target": "@orchestrator", "delivered": delivered}


def escalate_to_user(
    task: _tasks.Task,
    reason: str,
    project_root: Path,
    paths: Paths,
    *,
    telegram_sender: Callable[[str], bool] | None = None,
) -> dict:
    telegram_sender = telegram_sender or _default_telegram_sender()
    body = (
        f"URGENT stale task: {task.id} ({reason}) — "
        f"{task.title}; ping_count={task.ping_count}; "
        f"last update: {_last_update_line(task.body) or '(none)'}"
    )
    try:
        delivered = bool(telegram_sender(body))
    except Exception:
        delivered = False
    return {"action": "escalated-user", "target": "@user", "delivered": delivered}


def archive_done_task(
    task: _tasks.Task,
    project_root: Path,
    *,
    reason: str = "consolidation cleanup",
) -> dict:
    try:
        _tasks.complete_task(task.id, reason, project_root)
        return {"action": "archived", "target": "", "delivered": True}
    except Exception as e:  # pragma: no cover - defensive
        return {"action": f"error:{e}", "target": "", "delivered": False}


def archive_abandoned_task(
    task: _tasks.Task,
    project_root: Path,
    *,
    reason: str = "orphan task aged past abandon window",
) -> dict:
    """Move a terminal ABANDONED task into ``.tasks/archive/_abandoned/``."""
    try:
        _tasks.abandon_task(task.id, reason, project_root)
        return {"action": "archived-abandoned", "target": "", "delivered": True}
    except Exception as e:  # pragma: no cover - defensive
        return {"action": f"error:{e}", "target": "", "delivered": False}


def _default_sender() -> Callable[..., object]:
    # Lazy import to keep consolidate importable in minimal contexts.
    from . import messages as _messages

    def send(target: str, label: str, body: str, from_agent: str, *, paths: Paths):
        return _messages.send_message(
            target, label, body, from_agent, paths=paths, wake=False
        )

    return send


def _default_telegram_sender() -> Callable[[str], bool]:
    def send(body: str) -> bool:
        try:
            from . import telegram as _tg
            # Best-effort: many install shapes expose different entrypoints.
            fn = getattr(_tg, "send_user_message", None) or getattr(_tg, "send", None)
            if fn is None:
                return False
            fn(body)
            return True
        except Exception:
            return False
    return send


# ---------------------------------------------------------------------------
# Apply (verdict → action)
# ---------------------------------------------------------------------------


def apply_verdict(
    task: _tasks.Task,
    verdict: str,
    project_root: Path,
    paths: Paths,
    *,
    dry_run: bool = False,
    ping_escalate_threshold: int = PING_ESCALATE_THRESHOLD_DEFAULT,
    sender: Callable[..., object] | None = None,
    telegram_sender: Callable[[str], bool] | None = None,
) -> dict:
    """Dispatch verdict → side effect. Returns a result dict for rendering."""
    result: dict = {
        "task_id": task.id,
        "title": task.title,
        "verdict": verdict,
        "action": "noop",
        "target": "",
        "delivered": False,
        "dry_run": dry_run,
    }

    if verdict in (VERDICT_ACTIVE, VERDICT_BLOCKED, VERDICT_PAUSED):
        pass  # no action — paused/blocked tasks don't get re-pinged
    elif verdict == VERDICT_DONE:
        if dry_run:
            result["action"] = "would-archive"
        else:
            result.update(archive_done_task(task, project_root))
    elif verdict == VERDICT_ABANDONED:
        if dry_run:
            result["action"] = "would-archive-abandoned"
        else:
            result.update(archive_abandoned_task(task, project_root))
    elif verdict == VERDICT_UNOWNED:
        reason = "unowned"
        # Threshold: after N escalations without an owner assignment,
        # stop pinging @orchestrator. Otherwise the task re-escalates
        # every cooldown window forever and the inbox fills up with
        # identical !info messages. Mirrors the STALE behaviour.
        if task.ping_count >= ping_escalate_threshold:
            if dry_run:
                result["action"] = "noop-pinged-out"
            else:
                # Silent no-op: task stays in place, just stops bugging
                # us. Operator can assign, archive, or revisit anytime.
                result["action"] = "noop-pinged-out"
                # Bump ping_count once more so this branch stays hit.
                _bump_ping(task, project_root)
        else:
            if dry_run:
                result["action"] = "would-escalate-orchestrator"
                result["target"] = "@orchestrator"
            else:
                result.update(escalate_to_orchestrator(task, reason, project_root, paths, sender=sender))
    elif verdict == VERDICT_STALE:
        # Three-phase ladder, mirroring MSG_VERDICT_STALE
        # (consolidate.py:1011-1039) and the c8a5110 message-side fix:
        #   ping_count <  threshold   → ping persistent / escalate orch
        #   ping_count == threshold   → escalate to @user (last resort, once)
        #   ping_count >  threshold   → silent (noop-pinged-out)
        # Without the third arm the task re-escalates to @user every
        # cooldown cycle forever (witnessed 2026-04-25T19:00Z+:
        # 26 stale tasks each escalating 4×/h, 104 escalations/h
        # flooding @user via telegram).
        reason = f"stale>{STALE_WINDOW_MINUTES_DEFAULT}m"
        if task.ping_count > ping_escalate_threshold:
            result["action"] = "noop-pinged-out"
            # Bump ping_count once more so this branch stays hit and
            # the task doesn't drift back to a lower-arm classification
            # if something else mutates the task without resetting it.
            _bump_ping(task, project_root)
        elif task.ping_count == ping_escalate_threshold:
            if dry_run:
                result["action"] = "would-escalate-user"
                result["target"] = "@user"
            else:
                result.update(escalate_to_user(
                    task, reason, project_root, paths, telegram_sender=telegram_sender
                ))
                # escalate_to_user does not bump ping_count itself;
                # bump explicitly so the next fire moves to the
                # noop-pinged-out arm rather than re-firing here.
                _bump_ping(task, project_root)
        elif _is_persistent_agent(task.assignee, paths):
            if dry_run:
                result["action"] = "would-ping"
                result["target"] = task.assignee
            else:
                result.update(ping_persistent_agent(task, project_root, paths, sender=sender))
        else:
            if dry_run:
                result["action"] = "would-escalate-orchestrator"
                result["target"] = "@orchestrator"
            else:
                result.update(escalate_to_orchestrator(task, reason, project_root, paths, sender=sender))

    # Emit an event when something actually happened. Skip pure
    # "noop" actions (ACTIVE/BLOCKED/PAUSED tasks classified, no
    # side effect taken) — at one consolidate fire every 5 minutes
    # × N active tasks, those events drown out actionable signal
    # (measured 12.5k/day on spot 2026-04-25, 78% of task events).
    # noop-pinged-out is preserved because it carries throttle
    # signal; archives, escalations, pings remain emitted as before.
    if result["action"] != "noop":
        try:
            log_event(
                "task.consolidate",
                f"{task.id}: {verdict} → {result['action']}",
                meta={
                    "task_id": task.id,
                    "title": task.title,
                    "verdict": verdict,
                    "action": result["action"],
                    "target": result.get("target", ""),
                    "dry_run": dry_run,
                    "ping_count": task.ping_count,
                },
                paths=paths,
            )
        except Exception:
            pass

    return result


# ---------------------------------------------------------------------------
# Message lifecycle
# ---------------------------------------------------------------------------


def classify_message(
    msg: _messages.Message,
    *,
    now: _dt.datetime | None = None,
    stale_window_minutes: int = STALE_WINDOW_MINUTES_DEFAULT,
    info_archive_after_minutes: int | None = None,
    paths: "Paths | None" = None,
) -> str:
    """Return one of the MSG_VERDICT_* constants for ``msg``."""
    now = now or _utcnow()
    window = _dt.timedelta(minutes=stale_window_minutes)
    if info_archive_after_minutes is None:
        info_archive_after_minutes = INFO_AUTO_ARCHIVE_AFTER_MINUTES

    # Messages from @consolidate itself are meta-signals about other
    # messages and tasks (escalations, pings). They must never re-enter
    # the consolidation loop — if they did, each tick would escalate
    # the previous tick's escalations, producing geometric cascade
    # growth. Keep them visible in the heartbeat for one tick, then
    # auto-archive so the inbox doesn't slowly fill with transient
    # meta-chatter.
    if (msg.from_ or "").lstrip("@") == "consolidate":
        created = _parse_iso(msg.created)
        if created and (now - created) >= _dt.timedelta(minutes=5):
            return MSG_VERDICT_INFO_AUTO_ARCHIVE
        return MSG_VERDICT_PINNED

    # DONE-PENDING-ARCHIVE: already completed, still sitting in inbox/.
    # This check fires BEFORE the pinned-label check because completing
    # a message IS the explicit action that unpins it — once acted on,
    # even a !task or !query should archive. The previous order left
    # completed pinned messages stuck in inbox forever (witnessed
    # 2026-04-11 — !task messages closed via `messages done` at 19:18Z
    # were still showing in heartbeats hours later because PINNED
    # short-circuited before COMPLETED).
    if msg.status == _messages.STATUS_COMPLETED:
        return MSG_VERDICT_DONE_PENDING_ARCHIVE

    # Pinned labels: never touched by the consolidator beyond reporting.
    if msg.label in _messages.PINNED_LABELS:
        return MSG_VERDICT_PINNED

    # !done terminal check — must precede the STATUS_UNREAD branch so
    # unread !done notifications don't bounce through UNREAD-OLD → ping
    # → STALE forever (2026-04-15 self-audit gap (b)). Aging anchor is
    # read_at when the recipient viewed it; created when they didn't.
    if msg.label == "!done":
        info_window = _dt.timedelta(minutes=info_archive_after_minutes)
        read_at_for_done = _parse_iso(msg.read_at)
        anchor = read_at_for_done or _parse_iso(msg.created)
        if anchor and (now - anchor) >= info_window:
            return MSG_VERDICT_DONE

    # UNREAD-OLD: status still unread after the stale window. Rare after
    # auto-mark-read on view, but catches messages on agents that
    # never render their inbox.
    if msg.status == _messages.STATUS_UNREAD:
        created = _parse_iso(msg.created)
        if created and (now - created) >= window:
            # Cooldown: if we already escalated this message recently,
            # leave alone. Without this, every 5-min consolidate tick
            # re-escalates the same old unread message forever.
            last_ping = _parse_iso(msg.last_pinged_at)
            if last_ping and (now - last_ping) < window:
                return MSG_VERDICT_ACTIVE
            return MSG_VERDICT_UNREAD_OLD
        return MSG_VERDICT_ACTIVE

    # From here on, status is STATUS_READ or STATUS_REPLIED.
    read_at = _parse_iso(msg.read_at)

    # INFO-AUTO-ARCHIVE: !info and !reply messages that have been read
    # long enough and haven't been explicitly acted on. !reply is
    # conversational — read-without-reply is a valid terminal state, the
    # recipient absorbed the response and the conversation either ends
    # or the recipient sends a fresh reply. Without this, !reply falls
    # through to STALE and escalates to @orchestrator forever (witnessed
    # 2026-04-25: 56 unique stale !reply messages each escalating once
    # per 18-min cooldown, ping_count 191-294, dominating the events log
    # post 547c8c2). STATUS_REPLIED is handled by its own branch below.
    if (
        msg.label in {"!info", "!reply"}
        and msg.status == _messages.STATUS_READ
        and read_at
    ):
        info_window = _dt.timedelta(minutes=info_archive_after_minutes)
        if (now - read_at) >= info_window and not msg.completed_at:
            return MSG_VERDICT_INFO_AUTO_ARCHIVE

    # (``!done`` terminal check moved above the STATUS_UNREAD branch
    #  so unread !dones still terminate — see the block there.)

    # Replied messages are already "handled" — archive after cooldown.
    if msg.status == _messages.STATUS_REPLIED:
        replied_at = _parse_iso(msg.replied_at)
        if replied_at and (now - replied_at) >= window:
            return MSG_VERDICT_INFO_AUTO_ARCHIVE
        return MSG_VERDICT_ACTIVE

    # STALE: read_at is older than stale window, message was never acted
    # on (no replied_at, no completed_at). Could mean the recipient
    # forgot to follow up. Ping them.
    if read_at and (now - read_at) >= window and not msg.replied_at and not msg.completed_at:
        # If the recipient has no reader (built-in system agent or
        # GC'd ephemeral), pinging just spawns another no-reader
        # message that itself ages into STALE — a self-sustaining loop.
        if _is_no_reader(msg.to, paths):
            return MSG_VERDICT_INFO_AUTO_ARCHIVE
        # Cooldown: if we pinged recently, leave alone.
        last_ping = _parse_iso(msg.last_pinged_at)
        if last_ping and (now - last_ping) < window:
            return MSG_VERDICT_ACTIVE
        return MSG_VERDICT_STALE

    return MSG_VERDICT_ACTIVE


def _ping_msg_recipient(
    msg: _messages.Message,
    paths: Paths,
    *,
    sender: Callable[..., object] | None = None,
) -> dict:
    sender = sender or _default_sender()
    target = msg.to or "@orchestrator"
    body = (
        f"stale message check on {msg.id}: read but not acted on. "
        f"label={msg.label}, from={msg.from_}, "
        f"read_at={msg.read_at or '(none)'}"
    )
    try:
        sender(target, "!query", body, "@consolidate", paths=paths)
        delivered = True
    except Exception:
        delivered = False
    if msg.path is not None:
        try:
            _messages.bump_ping(msg.path, msg.ping_count)
        except Exception:
            pass
    return {"action": "pinged", "target": target, "delivered": delivered}


def _escalate_msg_to_orchestrator(
    msg: _messages.Message,
    reason: str,
    paths: Paths,
    *,
    sender: Callable[..., object] | None = None,
) -> dict:
    sender = sender or _default_sender()
    body = (
        f"stale message review: {msg.id} ({reason}) — "
        f"label={msg.label}, from={msg.from_} → to={msg.to}, "
        f"created={msg.created}, status={msg.status}, "
        f"ping_count={msg.ping_count}"
    )
    try:
        sender("@orchestrator", "!info", body, "@consolidate", paths=paths)
        delivered = True
    except Exception:
        delivered = False
    if msg.path is not None:
        try:
            _messages.bump_ping(msg.path, msg.ping_count)
        except Exception:
            pass
    return {"action": "escalated-orchestrator", "target": "@orchestrator", "delivered": delivered}


def _archive_msg(msg: _messages.Message, reason: str) -> dict:
    if msg.path is None:
        return {"action": "noop", "target": "", "delivered": False}
    try:
        dest = _messages.archive_message(msg.path)
        return {"action": "archived", "target": str(dest), "delivered": True, "reason": reason}
    except Exception as e:  # pragma: no cover - defensive
        return {"action": f"error:{e}", "target": "", "delivered": False}


def apply_message_verdict(
    msg: _messages.Message,
    verdict: str,
    paths: Paths,
    *,
    dry_run: bool = False,
    ping_escalate_threshold: int = PING_ESCALATE_THRESHOLD_DEFAULT,
    sender: Callable[..., object] | None = None,
) -> dict:
    result: dict = {
        "msg_id": msg.id,
        "label": msg.label,
        "from": msg.from_,
        "to": msg.to,
        "verdict": verdict,
        "action": "noop",
        "target": "",
        "delivered": False,
        "dry_run": dry_run,
    }

    if verdict in (MSG_VERDICT_ACTIVE, MSG_VERDICT_PINNED):
        pass  # no action
    elif verdict == MSG_VERDICT_DONE_PENDING_ARCHIVE:
        if dry_run:
            result["action"] = "would-archive"
        else:
            result.update(_archive_msg(msg, "done-pending-archive"))
    elif verdict == MSG_VERDICT_INFO_AUTO_ARCHIVE:
        if dry_run:
            result["action"] = "would-archive"
        else:
            result.update(_archive_msg(msg, "info-auto-archive"))
    elif verdict == MSG_VERDICT_DONE:
        if dry_run:
            result["action"] = "would-archive"
        else:
            result.update(_archive_msg(msg, "done-auto-archive"))
    elif verdict == MSG_VERDICT_UNREAD_OLD:
        # Threshold: after N escalations with no progress, archive
        # instead of re-escalating forever. Matches STALE behaviour.
        if msg.ping_count >= ping_escalate_threshold:
            if dry_run:
                result["action"] = "would-archive"
            else:
                result.update(_archive_msg(msg, "unread-old-pinged-out"))
        else:
            if dry_run:
                result["action"] = "would-escalate-orchestrator"
                result["target"] = "@orchestrator"
            else:
                result.update(_escalate_msg_to_orchestrator(msg, "unread-old", paths, sender=sender))
    elif verdict == MSG_VERDICT_STALE:
        # Three-phase ladder, mirroring task UNOWNED-pinged-out
        # (consolidate.py:696-710):
        #   ping_count <  threshold   → ping the recipient
        #   ping_count == threshold   → escalate to @orchestrator (once)
        #   ping_count >  threshold   → silent (noop-pinged-out)
        # Without the third arm the message re-escalates every cooldown
        # cycle forever (witnessed 2026-04-25: 19 stuck !urgent
        # messages at ping_count 141-167, ~133 escalations / 3.4h
        # flooding @orchestrator's inbox).
        if msg.ping_count > ping_escalate_threshold:
            result["action"] = "noop-pinged-out"
            if msg.path is not None:
                try:
                    _messages.bump_ping(msg.path, msg.ping_count)
                except Exception:
                    pass
        elif msg.ping_count == ping_escalate_threshold:
            if dry_run:
                result["action"] = "would-escalate-orchestrator"
                result["target"] = "@orchestrator"
            else:
                result.update(_escalate_msg_to_orchestrator(msg, "stale-pinged-out", paths, sender=sender))
        else:
            if dry_run:
                result["action"] = "would-ping"
                result["target"] = msg.to
            else:
                result.update(_ping_msg_recipient(msg, paths, sender=sender))

    # Skip events for the pure-noop case (ACTIVE/PINNED messages
    # classified, no side effect). At ~30k/day on spot 2026-04-25
    # those drown the events log (55% of all events). Archives,
    # escalations, and pings still emit.
    if result["action"] != "noop":
        try:
            log_event(
                "message.consolidate",
                f"{msg.id}: {verdict} → {result['action']}",
                meta={
                    "msg_id": msg.id,
                    "label": msg.label,
                    "verdict": verdict,
                    "action": result["action"],
                    "target": result.get("target", ""),
                    "dry_run": dry_run,
                    "ping_count": msg.ping_count,
                },
                paths=paths,
            )
        except Exception:
            pass
    return result


# ---------------------------------------------------------------------------
# Top-level pass
# ---------------------------------------------------------------------------


@dataclass
class ConsolidateReport:
    stale_window_minutes: int
    since: str
    dry_run: bool
    results: list[dict] = field(default_factory=list)
    message_results: list[dict] = field(default_factory=list)
    gc_results: list[dict] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.results:
            out[r["action"]] = out.get(r["action"], 0) + 1
        return out

    def message_counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.message_results:
            out[r["action"]] = out.get(r["action"], 0) + 1
        return out


def _gc_ephemeral_agents(
    paths: Paths,
    *,
    dry_run: bool = False,
) -> list[dict]:
    """Remove dead ephemeral agent directories, preserving useful output.

    An agent is eligible for GC if:
    - It has no MISSION.md (ephemeral, not persistent)
    - Its status starts with "complete" OR it has no alive tmux session
      and no pid file pointing to a running process

    Preserved before deletion:
    - output.log, report.md, harness.md → appended to a daily GC log
    - task completion status → logged as event
    """
    from . import agents as _agents
    from .events import log_event

    if not paths.agents.is_dir():
        return []

    results: list[dict] = []

    for entry in sorted(paths.agents.iterdir()):
        if not entry.is_dir() or not entry.name.startswith("@"):
            continue

        # Skip persistent agents. Either marker is sufficient; bootstrap
        # writes persona-index.md before MISSION.md, so a partially-
        # written persona dir must still be exempt (see the 2026-04-14
        # incident where 9 in-flight bootstraps got reaped as "dead").
        if (entry / "MISSION.md").is_file() or (entry / "persona-index.md").is_file():
            continue

        agent_name = entry.name
        status = ""
        try:
            status = (entry / "status").read_text(encoding="utf-8").strip()
        except (OSError, FileNotFoundError):
            pass

        # Check if agent is still running
        session = _agents.session_name_for(agent_name)
        is_alive = _agents.session_alive(session)

        # Check for running pid
        pid_alive = False
        pid_file = entry / "pid"
        if pid_file.is_file():
            try:
                import os
                pid = int(pid_file.read_text().strip())
                os.kill(pid, 0)  # Check if process exists
                pid_alive = True
            except (ValueError, OSError, ProcessLookupError):
                pass

        # Only GC if completed or dead (no session, no pid)
        is_complete = status.startswith("complete")
        is_dead = not is_alive and not pid_alive

        if not is_complete and not is_dead:
            continue  # Still running, leave it

        # Preserve useful output before deletion.
        #
        # Two lanes:
        #
        # 1. Bookkeeping (output.log, harness.md, task, status) gets
        #    concatenated into the per-agent log, truncated to 2KB each.
        #    These are context for "what was this agent" — the full file
        #    doesn't need to survive.
        #
        # 2. Deliverables (any other top-level .md file, matched
        #    case-insensitively on the extension) are the artifacts the
        #    agent was spawned to produce: audit reports, research
        #    notes, findings. They get preserved in full as sibling
        #    files under logs/agents/<project>/<agent-name>/. The
        #    concatenated log gets a pointer.
        #
        #    This lane exists because the old whitelist hardcoded
        #    "report.md" lowercase, and an audit agent that wrote its
        #    deliverable as REPORT.md had it silently rmtree'd with the
        #    rest of the agent_dir. Globbing by extension avoids that
        #    class of bug for any *.md name the agent chooses.
        preserved: dict[str, str] = {}
        for fname in (
            "output.log", "harness.md", "task", "status",
            "authority", "responsibility", "accountability",
            "spawned_at", "parent",
        ):
            fpath = entry / fname
            if fpath.is_file():
                try:
                    content = fpath.read_text(encoding="utf-8")
                    if content.strip():
                        preserved[fname] = content
                except (OSError, UnicodeDecodeError):
                    pass

        deliverables: dict[str, str] = {}
        for child in sorted(entry.iterdir()):
            if not child.is_file():
                continue
            lname = child.name.lower()
            if lname == "harness.md":
                continue  # bookkeeping, already captured above
            if not lname.endswith(".md"):
                continue
            try:
                content = child.read_text(encoding="utf-8")
                if content.strip():
                    deliverables[child.name] = content
            except (OSError, UnicodeDecodeError):
                pass

        # Preserve output under logs/agents/<project>/<agent-name>.log
        if (preserved or deliverables) and not dry_run:
            project_name = ""
            try:
                project_name = (entry / "project").read_text(encoding="utf-8").strip()
            except (OSError, FileNotFoundError):
                pass
            agent_log_dir = paths.logs / "agents" / (project_name or "_global")
            agent_log_dir.mkdir(parents=True, exist_ok=True)
            agent_log = agent_log_dir / f"{agent_name}.log"
            with open(agent_log, "a", encoding="utf-8") as f:
                f.write(f"# {agent_name} — {_utcnow().isoformat()}\n")
                f.write(f"Status: {status}\n")
                f.write(f"Reason: {'completed' if is_complete else 'dead (no session/pid)'}\n\n")
                for fname, content in preserved.items():
                    f.write(f"--- {fname} ---\n")
                    f.write(content[:2048])
                    if len(content) > 2048:
                        f.write(f"\n... (truncated, {len(content)} bytes total)\n")
                    f.write("\n")
                if deliverables:
                    f.write(f"--- deliverables (preserved in full at {agent_name}/) ---\n")
                    for dname in sorted(deliverables):
                        f.write(f"  {dname} ({len(deliverables[dname])} bytes)\n")
                    f.write("\n")
                f.write("\n")

            if deliverables:
                deliv_dir = agent_log_dir / agent_name
                deliv_dir.mkdir(parents=True, exist_ok=True)
                for dname, content in deliverables.items():
                    (deliv_dir / dname).write_text(content, encoding="utf-8")

        # Delete the directory
        if not dry_run:
            import shutil
            shutil.rmtree(entry, ignore_errors=True)

        reason = "completed" if is_complete else "dead"
        results.append({
            "agent": agent_name,
            "reason": reason,
            "status": status,
            "preserved_files": list(preserved.keys()) + list(deliverables.keys()),
        })

        log_event(
            "agent.gc",
            f"{agent_name} cleaned up ({reason})",
            agent=agent_name,
            paths=paths,
        )

    return results


def run_pass(
    *,
    project_root: Path | None = None,
    since: str = DEFAULT_SINCE,
    stale_window_minutes: int = STALE_WINDOW_MINUTES_DEFAULT,
    ping_escalate_threshold: int = PING_ESCALATE_THRESHOLD_DEFAULT,
    abandoned_age_days: int = ABANDONED_AGE_DAYS_DEFAULT,
    dry_run: bool = False,
    paths: Paths | None = None,
    sender: Callable[..., object] | None = None,
    telegram_sender: Callable[[str], bool] | None = None,
) -> ConsolidateReport:
    """One full lifecycle consolidation pass over the repo."""
    paths = paths or resolve()
    project_root = Path(project_root) if project_root else paths.project_root

    tasks_found = scan_active_tasks()
    commits = _git_log(project_root, since)

    now = _utcnow()
    report = ConsolidateReport(
        stale_window_minutes=stale_window_minutes, since=since, dry_run=dry_run
    )

    for t in tasks_found:
        # Git-commit soft signal: if a recent commit references the slug,
        # treat that commit's date as a touch on updated_at.
        evidence = _commit_touches(t, commits)
        if evidence:
            sha, iso = evidence
            commit_dt = _parse_iso(iso)
            task_dt = _parse_iso(t.updated)
            if commit_dt and (not task_dt or commit_dt > task_dt):
                t.updated = commit_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

        verdict = classify_task(
            t,
            now=now,
            stale_window_minutes=stale_window_minutes,
            ping_escalate_threshold=ping_escalate_threshold,
            abandoned_age_days=abandoned_age_days,
            paths=paths,
        )
        result = apply_verdict(
            t, verdict, project_root, paths,
            dry_run=dry_run,
            ping_escalate_threshold=ping_escalate_threshold,
            sender=sender,
            telegram_sender=telegram_sender,
        )
        if evidence:
            result["commit_touch"] = evidence[0]
        report.results.append(result)

    # Message lifecycle pass — same engine, parallel verdict path.
    msgs_found = _messages.scan_inbox_messages()
    for mm in msgs_found:
        mverdict = classify_message(
            mm, now=now, stale_window_minutes=stale_window_minutes, paths=paths
        )
        mresult = apply_message_verdict(
            mm, mverdict, paths,
            dry_run=dry_run,
            ping_escalate_threshold=ping_escalate_threshold,
            sender=sender,
        )
        report.message_results.append(mresult)

    # Ephemeral agent cleanup — remove dead one-shot agent directories,
    # preserving any useful output first.
    gc_results = _gc_ephemeral_agents(paths, dry_run=dry_run)
    report.gc_results = gc_results

    return report
