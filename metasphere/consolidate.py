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
# PARKED: trigger-gated / time-deferred. Like PAUSED (no ping, escalate,
# or abandon) but auto-expiring — set by wake_after/trigger rather than a
# manual status, so a legitimately-waiting task (#159 dogfood: the
# re-enable-mv task blocked ~9-11h on a log marker) isn't false-staled.
VERDICT_PARKED = "PARKED"
VERDICT_UNOWNED = "UNOWNED"
VERDICT_ABANDONED = "ABANDONED"
VERDICT_DONE = "DONE"

VERDICTS = (
    VERDICT_ACTIVE,
    VERDICT_STALE,
    VERDICT_BLOCKED,
    VERDICT_PAUSED,
    VERDICT_PARKED,
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
#: A pinned !task/!query that has aged past the conservative backstop TTL.
#: Soft-archived by the lifecycle drain. See :func:`_pinned_drain_verdict`.
MSG_VERDICT_PINNED_DRAINED = "MSG-PINNED-DRAINED"

MSG_VERDICTS = (
    MSG_VERDICT_ACTIVE,
    MSG_VERDICT_STALE,
    MSG_VERDICT_UNREAD_OLD,
    MSG_VERDICT_DONE_PENDING_ARCHIVE,
    MSG_VERDICT_INFO_AUTO_ARCHIVE,
    MSG_VERDICT_DONE,
    MSG_VERDICT_PINNED,
    MSG_VERDICT_PINNED_DRAINED,
)

#: Lifecycle drain for pinned !task/!query messages. Without it they short-
#: circuit to PINNED and accumulate forever: a dispatched task never leaves
#: UNREAD (sacred labels are never auto-read), so every scan/monitor/agent
#: dispatch piles up (~3,600 accumulated by 2026-07-27, 91% of them >21 days
#: old). The drain archives only messages that are genuinely resolved or so
#: old they are definitively abandoned/superseded — never a recent un-acted
#: task ("when in doubt, keep it"). Archive is SOFT (move to
#: archive/YYYY-MM-DD/) and reversible; a wrongly-archived task can be restored.
#:
#: Note on the shared bucket: a dispatched !task's actionable copy lives in the
#: TARGET's inbox, which for a globally-scoped agent is the shared global
#: bucket the sender also reads — so archiving it removes the target's copy
#: too. That is why the drain is age/resolution-gated, NOT "it's only a sender
#: copy": the backstop TTL is long enough that anything past it is done or
#: abandoned for its target as well.
PINNED_DRAIN_BACKSTOP_TTL_DAYS = 14

# Info messages are auto-archived once they've been read for more than
# this long. They're just notifications; nothing acts on them.
INFO_AUTO_ARCHIVE_AFTER_MINUTES = 60

#: Labels that REQUIRE an explicit response — reply, completion, or
#: ping ladder. Everything else is treated as notification-shaped and
#: auto-archived after :data:`READ_ARCHIVE_AFTER_DAYS` of silence.
#: Opt-out beats opt-in: the harness invents ad-hoc labels constantly
#: (!ack, !vet-result, !standby, !critic-clear, !poller-conflict-*,
#: ...) and an enumerated allowlist always lags reality. !task and
#: !query also live in :data:`metasphere.messages.PINNED_LABELS` and
#: short-circuit to PINNED earlier; they're listed here too so the
#: rule reads correctly in isolation. !urgent is the only label that
#: is required-action but not pinned — it reaches the STALE branch
#: and ping-ladders normally.
REQUIRED_ACTION_LABELS = frozenset({"!task", "!query", "!urgent"})

#: Generic read-and-silent auto-archive window. Days, not minutes —
#: a sender of a non-required-action message has 3 days to either
#: reply, complete it, or upgrade the label.
READ_ARCHIVE_AFTER_DAYS = 3

#: Hard ceiling on a !urgent message that has already run the full
#: escalation ladder (ping_count past threshold, orchestrator already
#: notified) and continues to age in inbox. Beyond this we treat the
#: original urgency as resolved-by-attrition and archive instead of
#: cycling the noop-pinged-out arm forever.
URGENT_LADDER_EXHAUSTED_ARCHIVE_AFTER_DAYS = 7

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

# Git lookback window for slug-matching shipped-evidence commits.
DEFAULT_SINCE = "2d"

# An ASSIGNED task that has been pinged out past the escalate threshold
# AND whose body has had no real ``## Updates`` line for this many days
# is a dead-task candidate: escalate ONCE to @orchestrator (keep/close),
# then ABANDON if still untouched. Replaces the perpetual
# noop-pinged-out cycle (ping_count 5,000–5,900 observed, 2026-06-21
# audit).
ASSIGNED_PINGED_OUT_STALE_DAYS = 7

# Parked / trigger-gated tasks (#159 follow-up). A task with ``wake_after``
# in the future is PARKED (fully quiet). Once wake_after passes, a task
# that ALSO names a ``trigger`` gets this many hours of additional grace
# (the hard ceiling, anchored at wake_after) so a slightly-late trigger
# isn't immediately staled — after which the normal ladder resumes so a
# forgotten park can't hide a dead task forever.
PARKED_TRIGGER_GRACE_HOURS = 48

# UNOWNED tasks: don't escalate-to-orchestrator on the first pass. Only
# escalate normal-or-higher priority tasks, and only after this grace
# window from creation, so a human/lead has time to claim a freshly-filed
# task first (#159 dogfood: an unassigned task got instant-escalated on
# creation). Below-normal (!low) UNOWNED tasks never escalate.
UNOWNED_ESCALATE_GRACE_MINUTES = 60


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


def _git_log(
    project_root: Path, since: str, ref: str | None = None
) -> list[tuple[str, str, str, str]]:
    """Return ``[(sha, iso_date, subject, body)]`` for commits in the window.

    When ``ref`` is given, only commits reachable from that ref are
    returned (used to restrict shipped-evidence to the default branch).
    """
    sep = "\x1e"
    fmt = f"%H%x09%cI%x09%s%x09%b{sep}"
    cmd = ["git", "-C", str(project_root), "log",
           f"--since={_normalize_since(since)}", f"--pretty=format:{fmt}"]
    if ref:
        cmd.append(ref)
    try:
        out = subprocess.check_output(
            cmd,
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


# Shipped-evidence is only trustworthy when the slug is specific enough
# that a word-boundary match in a commit message is unlikely to be a
# coincidence. A degenerate single-word slug like ``task`` matches ANY
# commit mentioning that word (e.g. ``feat: task system``), writing a
# spurious shipped-candidate escalation to @orchestrator on unrelated
# work (the @x fossil's ``slug='task'`` false positive, 2026-06-21).
# Require a structured multi-word kebab slug: at least one hyphen and a
# modest length floor. Real auto-generated task slugs are long kebab
# (``slugify`` joins title words with ``-``); the short ones that pass
# here ('code-task', 'wip-task' in tests) still carry ≥2 distinct words.
_MIN_EVIDENCE_SLUG_LEN = 6


def _is_reliable_evidence_slug(slug: str) -> bool:
    """True if ``slug`` is specific enough to treat a commit-message match
    as shipped evidence (multi-word kebab, not a single generic word)."""
    return len(slug) >= _MIN_EVIDENCE_SLUG_LEN and "-" in slug


def _commit_touches(
    task: _tasks.Task, commits: list[tuple[str, str, str, str]]
) -> tuple[str, str] | None:
    """If any commit references the task slug, return (sha, iso_date) of the newest."""
    slug = task.id
    if not slug or not _is_reliable_evidence_slug(slug):
        return None
    pat = _slug_pattern(slug)
    best: tuple[str, str] | None = None
    for sha, iso, subject, body in commits:
        if pat.search(f"{subject}\n{body}"):
            if best is None or iso > best[1]:
                best = (sha[:12], iso)
    return best


def _default_branch(project_root: Path) -> str:
    """Best-effort name of the repo's default branch.

    Tries ``origin/HEAD`` symref first (e.g. ``origin/main`` → ``main``),
    then probes ``main`` / ``master`` locally, then falls back to
    ``HEAD``. Used so a slug-matching commit only counts as shipped
    evidence when it is on the default branch — a commit on a feature
    branch is work-in-progress, not a close signal.
    """
    try:
        out = subprocess.check_output(
            ["git", "-C", str(project_root), "symbolic-ref", "--quiet",
             "--short", "refs/remotes/origin/HEAD"],
            text=True, stderr=subprocess.DEVNULL,
        ).strip()
        if out:
            return out.split("/", 1)[1] if "/" in out else out
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    for cand in ("main", "master"):
        try:
            subprocess.check_output(
                ["git", "-C", str(project_root), "rev-parse", "--verify",
                 "--quiet", cand],
                text=True, stderr=subprocess.DEVNULL,
            )
            return cand
        except (subprocess.CalledProcessError, FileNotFoundError):
            continue
    return "HEAD"


def _last_body_update_dt(body: str) -> _dt.datetime | None:
    """Datetime of the most recent ``- <iso> …`` line under ``## Updates``.

    The consolidator's ping bump never writes body lines, so the newest
    ``## Updates`` timestamp is the truest signal of real task activity
    (the audit used exactly this to see through ``updated_at`` masking).
    Returns ``None`` when there is no parseable update line.
    """
    if "## Updates" not in body:
        return None
    section = body.split("## Updates", 1)[1]
    best: _dt.datetime | None = None
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("- "):
            continue
        head = line[2:].split(None, 1)
        if not head:
            continue
        dt = _parse_iso(head[0])
        if dt and (best is None or dt > best):
            best = dt
    return best


# Escalation markers: small state files under
# ``~/.metasphere/state/task_escalations/`` recording that the
# consolidator has already escalated a task ONCE for a given reason, so
# the every-5-min pass doesn't re-fire the same !info forever. Two kinds:
#   ``shipped``        — value = the shipping commit sha (re-escalate on
#                        a *different* sha; skip on the same one).
#   ``stale_assigned`` — value = ISO time of the escalation (used to
#                        decide when an assigned-pinged-out task may be
#                        abandoned, and to reset on later activity).

def _escalation_marker_path(paths: Paths, kind: str, task_id: str) -> Path:
    return paths.state / "task_escalations" / f"{kind}.{task_id}"


def _read_escalation_marker(paths: Paths, kind: str, task_id: str) -> str | None:
    try:
        return _escalation_marker_path(paths, kind, task_id).read_text(
            encoding="utf-8"
        ).strip()
    except (OSError, FileNotFoundError):
        return None


def _write_escalation_marker(
    paths: Paths, kind: str, task_id: str, value: str
) -> None:
    try:
        p = _escalation_marker_path(paths, kind, task_id)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(value + "\n", encoding="utf-8")
    except OSError:
        pass


def _clear_escalation_marker(paths: Paths, kind: str, task_id: str) -> None:
    try:
        _escalation_marker_path(paths, kind, task_id).unlink()
    except (OSError, FileNotFoundError):
        pass


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


def _is_parked(task: _tasks.Task, now: _dt.datetime) -> bool:
    """True if ``task`` is trigger-gated / time-deferred and still within
    its parked window — consolidate should leave it fully alone.

    Opt-in via ``wake_after`` (ISO ts) and optionally ``trigger`` (freeform):

    - ``now < wake_after``  → parked (waiting for its ETA).
    - ``wake_after`` passed but a ``trigger`` is named → parked for up to
      :data:`PARKED_TRIGGER_GRACE_HOURS` past ``wake_after`` (hard ceiling),
      so a slightly-late trigger isn't immediately staled.
    - no ``wake_after`` but a ``trigger`` is named → parked for up to
      :data:`PARKED_TRIGGER_GRACE_HOURS` from the last real activity
      (``updated`` / ``created``), a bounded grace for a timestamp-less park.

    Past the ceiling the normal stale ladder resumes, so a forgotten park
    can never hide a dead task indefinitely.
    """
    wake = _parse_iso(task.wake_after)
    if wake is not None and now < wake:
        return True
    trigger = (task.trigger or "").strip()
    if trigger:
        anchor = wake or _parse_iso(task.updated) or _parse_iso(task.created)
        if anchor is not None and (now - anchor) < _dt.timedelta(
            hours=PARKED_TRIGGER_GRACE_HOURS
        ):
            return True
    return False


def _priority_rank(priority: str) -> int:
    """Rank a priority for comparison; lower = more urgent. Unknown →
    treated as ``!normal``."""
    order = {"!urgent": 0, "!high": 1, "!normal": 2, "!low": 3}
    return order.get((priority or "").strip().lower(), 2)


def _unowned_should_escalate(task: _tasks.Task, now: _dt.datetime) -> bool:
    """Whether an UNOWNED task is worth escalating to @orchestrator yet.

    Don't escalate on the first pass: only normal-or-higher priority, and
    only after :data:`UNOWNED_ESCALATE_GRACE_MINUTES` from creation, so a
    human/lead has a window to claim a freshly-filed task before the inbox
    gets a !info about it (#159 dogfood). ``!low`` UNOWNED never escalates.
    """
    if _priority_rank(task.priority) > _priority_rank("!normal"):
        return False  # below normal (!low) — never escalate, just sit
    created = _parse_iso(task.created)
    if created is None:
        return True  # no creation stamp — don't suppress indefinitely
    return (now - created) >= _dt.timedelta(minutes=UNOWNED_ESCALATE_GRACE_MINUTES)


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
    # (the bug operator-flagged 2026-04-15T08:55Z that drove 8
    # STALE→escalated-user events per 15-min cycle on a project's
    # paused tasks).
    if status.startswith("paused"):
        return VERDICT_PAUSED

    # PARKED: trigger-gated / time-deferred. Checked BEFORE the stale
    # window so a legitimately-waiting task (blocked on a long-running job
    # / a log marker / an external ETA) doesn't get pinged STALE and
    # marched up the escalate→abandon ladder while it waits (#159 dogfood:
    # the re-enable-mv task, ~9-11h on a '>> Phase 1 done.' marker, got
    # false-staled within ~25min). A task opts in by setting wake_after
    # (and optionally a freeform trigger):
    #   • now < wake_after            → PARKED (fully quiet)
    #   • wake_after passed, trigger  → PARKED up to wake_after +
    #       set, within grace ceiling   PARKED_TRIGGER_GRACE_HOURS
    #   • else                        → fall through to the normal ladder
    # The grace ceiling means a parked task can't suppress its lifecycle
    # forever — past it, staleness becomes meaningful again.
    if _is_parked(task, now):
        return VERDICT_PARKED

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
    # Verified live 2026-04-25T10:00Z: 25 orphan-assignee tasks at
    # ping_count 280-294, age 4d, all assigned to project-team
    # ephemerals whose dirs were rmtree'd by the standard ephemeral GC.
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
    (~/.metasphere/agents/@x/) or under a project
    (~/.metasphere/projects/<proj>/agents/@x/), so both locations
    must be checked.
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
    """Write ``last_pinged_at`` (now) and increment ``ping_count``.

    Crucially does NOT bump ``updated_at`` — a ping is consolidator
    bookkeeping, not task progress. Bumping ``updated_at`` here made
    pinged-out tasks read as perpetually fresh-active in ``task list``,
    masking true staleness (2026-06-21 audit). ``last_pinged_at`` is the
    cooldown signal; ``updated_at`` stays at the last real edit.
    """
    now_iso = _utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
    return _tasks.update_task(
        task.id,
        project_root,
        bump_updated=False,
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

    Per operator directive (2026-04-15T08:55Z): route to the project's lead first so
    external collaborators don't spam the operator's view with pings for
    project-scoped tasks the operator doesn't own. Falls back to the
    task's ``assigned_to`` only when the project has no lead (or no
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


def _maybe_escalate_shipped_candidate(
    task: _tasks.Task,
    sha: str,
    project_root: Path,
    paths: Paths,
    *,
    dry_run: bool = False,
    sender: Callable[..., object] | None = None,
) -> dict:
    """A default-branch commit references the task slug — shipped evidence.

    Escalate ONCE to @orchestrator as a close-candidate (Fix #3) instead
    of bumping ``updated_at`` (which masked shipped work as perpetually
    fresh-active). The ``shipped`` marker stores the sha so the same
    commit doesn't re-escalate every 5-min pass; a *different*, newer
    sha re-escalates. Does NOT auto-close — the orchestrator decides.
    """
    prev = _read_escalation_marker(paths, "shipped", task.id)
    if prev == sha:
        return {"action": "noop", "target": ""}  # already flagged this sha
    if dry_run:
        return {"action": "would-escalate-shipped-candidate", "target": "@orchestrator"}
    sender = sender or _default_sender()
    body = (
        f"task {task.id} looks shipped via {sha} on the default branch — close?\n"
        f"title: {task.title}\n"
        f"last update: {_last_update_line(task.body) or '(none)'}"
    )
    try:
        sender("@orchestrator", "!info", body, "@consolidate", paths=paths)
        delivered = True
    except Exception:
        delivered = False
    _write_escalation_marker(paths, "shipped", task.id, sha)
    return {
        "action": "escalated-shipped-candidate",
        "target": "@orchestrator",
        "delivered": delivered,
    }


def _handle_assigned_pinged_out(
    task: _tasks.Task,
    project_root: Path,
    paths: Paths,
    *,
    dry_run: bool = False,
    sender: Callable[..., object] | None = None,
) -> dict:
    """Terminal handling for an assigned task pinged out past threshold.

    Was: silent ``noop-pinged-out`` forever (ping_count 5,000–5,900 over
    60–65 days). Now (Fix #4): if the task body has had no real
    ``## Updates`` line for > :data:`ASSIGNED_PINGED_OUT_STALE_DAYS`,
    escalate ONCE to @orchestrator (keep/close), then ABANDON on a later
    pass if still untouched. A real body update after the escalation
    resets the cycle. A body that is still fresh stays quietly pinged-out
    (the agent is demonstrably working).
    """
    now = _utcnow()
    last_update = _last_body_update_dt(task.body)
    body_fresh = (
        last_update is not None
        and (now - last_update) < _dt.timedelta(days=ASSIGNED_PINGED_OUT_STALE_DAYS)
    )
    if body_fresh:
        # The task recovered (real recent activity). Clear any prior
        # stale-assigned escalation marker so a future staleness starts
        # the escalate→abandon ladder fresh, and stay quietly pinged-out.
        if not dry_run:
            _clear_escalation_marker(paths, "stale_assigned", task.id)
            _bump_ping(task, project_root)
        return {"action": "noop-pinged-out", "target": "", "delivered": False}

    marker = _read_escalation_marker(paths, "stale_assigned", task.id)
    if marker is None:
        # First detection — escalate once, record the time.
        if dry_run:
            return {"action": "would-escalate-orchestrator", "target": "@orchestrator"}
        res = escalate_to_orchestrator(
            task,
            f"assigned + pinged-out + no update >{ASSIGNED_PINGED_OUT_STALE_DAYS}d — keep or close?",
            project_root, paths, sender=sender,
        )
        _write_escalation_marker(
            paths, "stale_assigned", task.id, now.strftime("%Y-%m-%dT%H:%M:%SZ")
        )
        return res

    marker_dt = _parse_iso(marker)
    # Activity after the escalation = it got attention. Reset and wait.
    if last_update is not None and marker_dt is not None and last_update > marker_dt:
        if not dry_run:
            _clear_escalation_marker(paths, "stale_assigned", task.id)
            _bump_ping(task, project_root)
        return {"action": "noop-pinged-out", "target": "", "delivered": False}
    # Give at least one full stale window between escalation and abandon
    # so the orchestrator has a real chance to act ("next cycle").
    if marker_dt is not None and (now - marker_dt) < _dt.timedelta(
        minutes=STALE_WINDOW_MINUTES_DEFAULT
    ):
        if not dry_run:
            _bump_ping(task, project_root)
        return {"action": "noop-pinged-out", "target": "", "delivered": False}
    # Still untouched after the grace window — abandon.
    if dry_run:
        return {"action": "would-archive-abandoned", "target": ""}
    res = archive_abandoned_task(
        task, project_root,
        reason=f"assigned-pinged-out, no update >{ASSIGNED_PINGED_OUT_STALE_DAYS}d, "
               "escalated then untouched",
    )
    _clear_escalation_marker(paths, "stale_assigned", task.id)
    return res


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

    if verdict in (VERDICT_ACTIVE, VERDICT_BLOCKED, VERDICT_PAUSED, VERDICT_PARKED):
        pass  # no action — active/blocked/paused/parked tasks aren't pinged
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
        elif not _unowned_should_escalate(task, _utcnow()):
            # Grace (#159 follow-up): don't escalate a freshly-filed or
            # below-normal UNOWNED task on the first pass — give a human a
            # window to claim it. Quiet, and no ping bump so the creation
            # age accrues naturally toward the grace threshold.
            result["action"] = "noop-unowned-grace"
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
            # Terminal handling (Fix #4): an assigned task pinged out past
            # threshold no longer loops on noop-pinged-out forever. It
            # escalates ONCE to @orchestrator, then abandons if still
            # untouched. _handle_assigned_pinged_out owns the ping bump.
            result.update(_handle_assigned_pinged_out(
                task, project_root, paths, dry_run=dry_run, sender=sender,
            ))
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
    # (~12.5k/day measured on a populated instance 2026-04-25,
    # 78% of task events).
    # noop-pinged-out is preserved because it carries throttle
    # signal; archives, escalations, pings remain emitted as before.
    # noop-unowned-grace is skipped too — it fires every tick for every
    # un-claimed fresh task and would re-introduce exactly that drown-out.
    if result["action"] not in ("noop", "noop-unowned-grace"):
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


def _pinned_drain_verdict(
    msg: _messages.Message,
    now: _dt.datetime,
    ttl_days: int,
) -> str | None:
    """Drain verdict for a pinned (!task/!query) message, or ``None`` to keep.

    Returns :data:`MSG_VERDICT_PINNED_DRAINED` ONLY when the message has aged
    past ``ttl_days`` — a single, conservative, age-based backstop. At two-plus
    weeks with no explicit close, a dispatched task is done or abandoned for its
    target too; the fire-and-forget dispatches (scans/monitors) and old inbound
    that never got a ``msg done`` are exactly this population (91% of the
    ~3,600 backlog was >21 days old on 2026-07-27).

    Deliberately does NOT drain on ``status == REPLIED``: a reply is often a
    clarifying question or an interim "on it" on a STILL-OPEN task, so treating
    any reply as "resolved" would archive genuinely-pending work (the hard
    "never drop an un-acted task" constraint). Explicit resolution is
    ``status == COMPLETED``, which is handled earlier in :func:`classify_message`
    and never reaches here. An unparseable ``created`` returns ``None`` (we
    can't prove it is old → keep it; when in doubt, keep it).
    """
    created = _parse_iso(msg.created)
    if created is None:
        return None  # can't prove age → keep (when in doubt, keep it)
    if (now - created) >= _dt.timedelta(days=ttl_days):
        return MSG_VERDICT_PINNED_DRAINED
    return None


def classify_message(
    msg: _messages.Message,
    *,
    now: _dt.datetime | None = None,
    stale_window_minutes: int = STALE_WINDOW_MINUTES_DEFAULT,
    info_archive_after_minutes: int | None = None,
    pinned_drain_ttl_days: int = PINNED_DRAIN_BACKSTOP_TTL_DAYS,
    paths: "Paths | None" = None,
) -> str:
    """Return one of the MSG_VERDICT_* constants for ``msg``.

    Branch precedence (most specific first):

    1. ``@consolidate``-from-self → PINNED for one tick, then
       INFO_AUTO_ARCHIVE.
    2. ``status == COMPLETED`` → DONE_PENDING_ARCHIVE.
    3. ``label`` in PINNED_LABELS (``!task``, ``!query``, ``!urgent``)
       → PINNED.
    4. ``label == "!done"``: aged past info_window → DONE; else if
       sender is ``@orchestrator`` and recipient is non-orch + has a
       reader → DONE_PENDING_ARCHIVE (thread-closer fast-path).
    5. ``status == UNREAD`` aged past stale_window → UNREAD_OLD (with
       ping cooldown).
    6. ``status == REPLIED`` aged past stale_window → INFO_AUTO_ARCHIVE.
    7. ``label`` in ``{!info, !reply}`` aged past info_window →
       INFO_AUTO_ARCHIVE.
    8. ``label`` not in :data:`REQUIRED_ACTION_LABELS`, read +
       :data:`READ_ARCHIVE_AFTER_DAYS` ago, no reply / completion →
       INFO_AUTO_ARCHIVE (generic catch-all for ad-hoc labels).
    9. Read for stale_window without action: STALE (with no-reader
       short-circuit to INFO_AUTO_ARCHIVE; non-required-action labels
       skip the ping ladder, the 3-day archive catches them).
    10. Otherwise → ACTIVE.
    """
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

    # Pinned labels: normally never touched by the consolidator beyond
    # reporting — the sacred-label guarantee that a dispatched task is never
    # auto-marked read. But that guarantee is why they accumulate forever
    # (~3,600 unread by 2026-07-27). Drain only the genuinely resolved or
    # definitively aged-out ones; a recent un-acted task stays PINNED.
    if msg.label in _messages.PINNED_LABELS:
        drained = _pinned_drain_verdict(msg, now, pinned_drain_ttl_days)
        if drained is not None:
            return drained
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
        # Orchestrator-sent !done to a non-orchestrator agent is a
        # thread-closer: the action is the implicit downstream work
        # (e.g. dispatching critic, dispatching merger after a
        # greenlight). Stale-pinging these as "read but not acted on"
        # generated 4+ false-positive escalations this session. Treat
        # as DONE-PENDING-ARCHIVE immediately — they don't need to age
        # through the stale window first. Skip when the recipient is
        # a no-reader (system agent or GC'd ephemeral): those have
        # their own INFO-AUTO-ARCHIVE path further down which is the
        # more aggressive cleanup, and the existing tests rely on it.
        from_norm = (msg.from_ or "").lstrip("@")
        to_norm = (msg.to or "").lstrip("@")
        if (
            from_norm == "orchestrator"
            and to_norm
            and to_norm != "orchestrator"
            and not _is_no_reader(msg.to, paths)
        ):
            return MSG_VERDICT_DONE_PENDING_ARCHIVE
        # Self-sent !done (sender == recipient) auto-archives ahead of
        # the info window. `msg done` on any message generates a !done
        # notification back to the sender; when sender == recipient
        # (e.g. @orchestrator marking its own outbound msg done) the
        # resulting !done loops the consolidator forever — each tick
        # ages it into UNREAD-OLD/STALE → ping → !query escalation,
        # which itself triggers another `msg done`.
        if from_norm and to_norm and from_norm == to_norm:
            return MSG_VERDICT_INFO_AUTO_ARCHIVE
        # !done addressed to a no-reader (system agent like @consolidate
        # or GC'd ephemeral) auto-archives immediately — nobody can act
        # on it, and pinging just spawns more no-reader messages.
        if _is_no_reader(msg.to, paths):
            return MSG_VERDICT_INFO_AUTO_ARCHIVE
        # All other !done messages: hold ACTIVE until they age into
        # DONE via the info_window check above. Falling through to the
        # UNREAD-OLD / STALE branches turned thread-closer !dones into
        # ping ladders — !done is a notification, not a request for
        # reply, so it should never enter the ping cycle.
        return MSG_VERDICT_ACTIVE

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

    # READ-AND-SILENT-AUTO-ARCHIVE: generic catch-all. Any read
    # message that has gone READ_ARCHIVE_AFTER_DAYS without reply,
    # completion, or escalation auto-archives — UNLESS its label is
    # in :data:`REQUIRED_ACTION_LABELS` (the only labels for which
    # silence is itself a problem worth escalating). Replaces the
    # ad-hoc TERMINAL_INFO_LABELS opt-in: an enumerated allowlist
    # always lags the labels callers invent (witnessed 2026-05-09:
    # !ack/!vet-result/!standby cycled STALE forever, then a fresh
    # round of !critic-clear/!alert/!poller-conflict-final showed up
    # the next tick).
    if (
        msg.status == _messages.STATUS_READ
        and read_at
        and not msg.completed_at
        and not msg.replied_at
        and msg.label not in REQUIRED_ACTION_LABELS
    ):
        archive_window = _dt.timedelta(days=READ_ARCHIVE_AFTER_DAYS)
        if (now - read_at) >= archive_window:
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
        # !info and !reply are notification-shaped — read-without-action
        # is a valid terminal state, and they have their own 60min
        # INFO-AUTO-ARCHIVE path above. Pinging them in the 15-60min
        # window between stale and auto-archive generates 3 noise
        # !query escalations per !info before the archive lands
        # (witnessed 2026-04-26 on two messages each pinged at
        # +15/+30/+45min before being archived at +60min). Skip the
        # ping ladder for these labels — the
        # auto-archive will catch them.
        # Only labels in REQUIRED_ACTION_LABELS reach the ping ladder.
        # !info/!reply and every other notification-shaped label
        # (!ack, !vet-result, !standby, !critic-clear, ad-hoc project
        # labels, ...) hold ACTIVE until the 3-day generic
        # read-and-silent archive above catches them. Without this,
        # the gap between the 15-min STALE window and the 3-day
        # archive emits ~280 noop-pinged events per message
        # (witnessed 2026-05-09).
        if msg.label not in REQUIRED_ACTION_LABELS:
            return MSG_VERDICT_ACTIVE
        # If the recipient has no reader (built-in system agent or
        # GC'd ephemeral), pinging just spawns another no-reader
        # message that itself ages into STALE — a self-sustaining loop.
        if _is_no_reader(msg.to, paths):
            return MSG_VERDICT_INFO_AUTO_ARCHIVE
        # !urgent ladder-exhausted exit: once ping_count has carried
        # the message past the orchestrator-escalation phase and the
        # message has been read for 7+ days, the original urgency has
        # either been absorbed into other work or the user has chosen
        # not to act. The noop-pinged-out arm in apply_message_verdict
        # would otherwise keep firing forever (witnessed pre-2026-05-09
        # cleanup: ping_count 100-680 across stuck !urgent messages).
        if (
            msg.label == "!urgent"
            and msg.ping_count > PING_ESCALATE_THRESHOLD_DEFAULT
            and (now - read_at) >= _dt.timedelta(
                days=URGENT_LADDER_EXHAUSTED_ARCHIVE_AFTER_DAYS
            )
        ):
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
    elif verdict == MSG_VERDICT_PINNED_DRAINED:
        if dry_run:
            result["action"] = "would-archive"
        else:
            result.update(_archive_msg(msg, "pinned-drained"))
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
    # classified, no side effect). At ~30k/day on a populated
    # instance 2026-04-25 those drown the events log (55% of all
    # events). Archives, escalations, and pings still emit.
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
    orphan_results: list[dict] = field(default_factory=list)

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

        # Check if agent is still running. ``_resolve_session`` is the
        # project-aware resolver — bare ``session_name_for`` would miss
        # project-scoped agents and could mark them dead while their
        # tmux session is still alive under
        # ``metasphere-<project>-<agent>``.
        from .session import _resolve_session
        session = _resolve_session(agent_name)
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
    pinned_drain_ttl_days: int = PINNED_DRAIN_BACKSTOP_TTL_DAYS,
    dry_run: bool = False,
    paths: Paths | None = None,
    sender: Callable[..., object] | None = None,
    telegram_sender: Callable[[str], bool] | None = None,
) -> ConsolidateReport:
    """One full lifecycle consolidation pass over the repo."""
    paths = paths or resolve()
    project_root = Path(project_root) if project_root else paths.project_root

    tasks_found = scan_active_tasks()
    # Only commits on the DEFAULT branch count as shipped evidence — a
    # slug match on a feature branch is work-in-progress, not a close
    # signal (Fix #3).
    commits = _git_log(project_root, since, ref=_default_branch(project_root))

    now = _utcnow()
    report = ConsolidateReport(
        stale_window_minutes=stale_window_minutes, since=since, dry_run=dry_run
    )

    for t in tasks_found:
        # Git-commit shipped evidence: a default-branch commit referencing
        # the slug is a CLOSE candidate, not a freshness bump. Escalate
        # once to @orchestrator instead of refreshing updated_at (which
        # masked shipped work as perpetually active — Fix #3). The task
        # still runs its normal lifecycle below until explicitly closed.
        evidence = _commit_touches(t, commits)
        shipped_result: dict | None = None
        if evidence:
            sha, _iso = evidence
            shipped_result = _maybe_escalate_shipped_candidate(
                t, sha, project_root, paths, dry_run=dry_run, sender=sender,
            )

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
            if shipped_result and shipped_result.get("action", "").startswith(
                ("escalated-shipped", "would-escalate-shipped")
            ):
                result["shipped_candidate"] = shipped_result["action"]
        report.results.append(result)

    # Message lifecycle pass — same engine, parallel verdict path.
    msgs_found = _messages.scan_inbox_messages()
    for mm in msgs_found:
        mverdict = classify_message(
            mm, now=now, stale_window_minutes=stale_window_minutes,
            pinned_drain_ttl_days=pinned_drain_ttl_days, paths=paths,
        )
        mresult = apply_message_verdict(
            mm, mverdict, paths,
            dry_run=dry_run,
            ping_escalate_threshold=ping_escalate_threshold,
            sender=sender,
        )
        report.message_results.append(mresult)

    # Outbox-orphan sweep — late-deliver sender-side-only messages the
    # bus never carried (hand-written outbox files, partial writes).
    # Silent loss otherwise: no inbox copy, no wake, unresolvable by
    # ``msg done`` (2026-07-05 20:52 incident). Guarded: the sweep
    # already swallows per-file failures, but a wholesale failure here
    # must not abort the GC work below or the report.
    try:
        report.orphan_results = _messages.sweep_outbox_orphans(
            paths, dry_run=dry_run,
        )
    except Exception:
        pass

    # Ephemeral agent cleanup — remove dead one-shot agent directories,
    # preserving any useful output first.
    gc_results = _gc_ephemeral_agents(paths, dry_run=dry_run)
    report.gc_results = gc_results

    return report
