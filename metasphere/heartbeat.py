"""Proactive monitoring daemon.

Walks the repo for urgent unread messages, agents in waiting/blocked
states, and urgent tasks; optionally invokes the orchestrator agent
with a freshly built context block (via tmux paste if a session is
live, otherwise via the configured runtime's headless mode).

State (which urgent items have already been notified about) lives in
``$METASPHERE_DIR/state/heartbeat_state`` and is mutated under
``metasphere.io.file_lock`` so concurrent ticks cannot tear lines.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import os
import subprocess
import time
from pathlib import Path

from .agents import (
    AgentRecord,
    list_agents,
    session_alive,
    touch_last_active,
)
from .context import build_context
from .events import log_event
from .io import file_lock
from .messages import Message, STATUS_UNREAD, collect_inbox, send_message
from .paths import Paths, resolve
from .tasks import list_tasks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _notify_user(text: str, paths: Paths) -> None:
    """Send a heartbeat-class notification to the user via Telegram.

    Failures are swallowed — heartbeat ticks must never raise. Looks up
    the chat id via the posthook resolver so the heartbeat and posthook
    share the same config-file precedence.
    """
    try:
        from .posthook import _resolve_chat_id
        from .telegram import api as telegram_api

        chat_id = _resolve_chat_id(paths)
        if chat_id is None:
            return
        telegram_api.send_message(chat_id, text)
    except Exception:
        # Swallow: notification is best-effort.
        pass


def _state_file(paths: Paths) -> Path:
    return paths.state / "heartbeat_state"


def _last_run_file(paths: Paths) -> Path:
    return paths.state / "heartbeat_last_run"


def _read_state_keys(paths: Paths) -> set[str]:
    p = _state_file(paths)
    if not p.is_file():
        return set()
    try:
        return {ln.strip() for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()}
    except OSError:
        return set()


def already_notified(paths: Paths, key: str) -> bool:
    return key in _read_state_keys(paths)


_STATE_COMPACT_THRESHOLD = 5000


def mark_notified(paths: Paths, key: str) -> None:
    """Append ``key`` to the dedupe state file under flock (idempotent).

    Append-only with lazy compaction: each new key is a single
    ``open(..., "a")`` write under flock — O(1) instead of rewriting
    the whole file. Compaction (dedupe + sort) only happens when the
    file exceeds ``_STATE_COMPACT_THRESHOLD`` lines, preserving forensic
    discovery order in the common case while keeping the file bounded.
    """
    p = _state_file(paths)
    p.parent.mkdir(parents=True, exist_ok=True)
    with file_lock(p.with_suffix(p.suffix + ".lock")):
        keys = _read_state_keys(paths)
        if key in keys:
            return
        with open(p, "a", encoding="utf-8") as fh:
            fh.write(key + "\n")
        # Lazy compaction.
        try:
            line_count = sum(1 for _ in p.open("r", encoding="utf-8"))
        except OSError:
            return
        if line_count > _STATE_COMPACT_THRESHOLD:
            try:
                deduped = _read_state_keys(paths)
                p.write_text("\n".join(sorted(deduped)) + "\n", encoding="utf-8")
            except OSError:
                pass


def clear_notified(paths: Paths, key: str) -> None:
    p = _state_file(paths)
    if not p.is_file():
        return
    with file_lock(p.with_suffix(p.suffix + ".lock")):
        keys = _read_state_keys(paths)
        if key not in keys:
            return
        keys.discard(key)
        p.write_text(("\n".join(sorted(keys)) + "\n") if keys else "", encoding="utf-8")


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------


def check_urgent_messages(paths: Paths) -> list[Message]:
    """Return all unread !urgent messages visible from ``paths.scope``."""
    msgs = collect_inbox(paths.scope, paths.project_root)
    return [m for m in msgs if m.label == "!urgent" and m.status == STATUS_UNREAD]


def check_blocked_agents(paths: Paths) -> list[AgentRecord]:
    """Return agents whose status begins with ``waiting:`` or ``blocked:``."""
    out: list[AgentRecord] = []
    for a in list_agents(paths):
        s = (a.status or "").strip()
        if s.startswith("waiting:") or s.startswith("blocked:"):
            out.append(a)
    return out


def check_urgent_tasks(paths: Paths) -> tuple[int, int]:
    """Return ``(urgent_count, total_count)`` of active tasks in scope."""
    items = list_tasks(paths.scope, paths.project_root, include_completed=False)
    urgent = sum(1 for t in items if t.priority == "!urgent")
    return urgent, len(items)


# ---------------------------------------------------------------------------
# QUESTIONS.md — "needs from the operator" reminder
#
# Design: internal design notes §1. QUESTIONS.md is the
# orchestrator-maintained canonical ledger of decisions/manual-actions
# blocking work (``$METASPHERE_DIR/state/QUESTIONS.md``). The morning
# briefing renders it from a prompt; this is the during-the-day reminder
# that pings the operator when a 🔴 (blocking) item — or an aged 🟡 — is open.
#
# SHIPPED DISABLED BY DEFAULT: this changes the operator's notification
# behaviour, and the reminder thresholds are still an open question to
# @orchestrator (proposal Q1). Gate on ``METASPHERE_QUESTIONS_ENABLED``;
# flip the default in a follow-up once the cadence is confirmed.
# ---------------------------------------------------------------------------


_RED, _AMBER, _GREEN = "🔴", "🟡", "🟢"
_QUESTION_FLAGS = (_RED, _AMBER, _GREEN)


@dataclasses.dataclass(frozen=True)
class Question:
    """One ``- <flag> ...`` bullet parsed from QUESTIONS.md."""

    project: str
    flag: str
    text: str
    raised: _dt.date | None

    def age_hours(self, now: _dt.date) -> float | None:
        """Age in hours, at *day* resolution.

        ``raised`` comes from a ``(YYYY-MM-DD)`` stamp in QUESTIONS.md — it
        carries no time-of-day — and ``now`` is a ``date``. So the only
        honest granularity is whole days; ``.days * 24`` is exact for these
        inputs, NOT a precision loss. Amber therefore ages in one calendar
        day after ``raised`` (``AMBER_AGE_H`` default 24h). Don't "fix" this
        to sub-day precision without first giving ``raised`` a real time.
        """
        if self.raised is None:
            return None
        return max(0.0, (now - self.raised).days * 24.0)

    def sig(self) -> str:
        import hashlib

        return hashlib.sha1(
            f"{self.project}|{self.text}".encode("utf-8")
        ).hexdigest()[:12]


def _questions_file(paths: Paths) -> Path:
    return paths.state / "QUESTIONS.md"


def parse_questions(text: str) -> list[Question]:
    """Parse QUESTIONS.md into flagged items.

    Stable, forgiving parse: project = the nearest ``## `` heading; an
    item is a ``- `` bullet whose first non-space glyph is a 🔴/🟡/🟢
    flag. Bullets led by any other glyph (✅ resolved, 🌙 notes, …) are
    skipped — only live, flagged questions ping. ``raised`` is the last
    ``(YYYY-MM-DD)`` on the line, if any.
    """
    import re

    items: list[Question] = []
    project = ""
    date_re = re.compile(r"\((\d{4})-(\d{2})-(\d{2})\)")
    for raw in text.splitlines():
        line = raw.rstrip()
        stripped = line.strip()
        if stripped.startswith("## "):
            project = stripped[3:].strip()
            continue
        if not stripped.startswith("- "):
            continue
        body = stripped[2:].lstrip()
        flag = body[:1]
        if flag not in _QUESTION_FLAGS:
            continue
        item_text = body[1:].strip()
        raised: _dt.date | None = None
        matches = date_re.findall(line)
        if matches:
            y, m, d = matches[-1]
            try:
                raised = _dt.date(int(y), int(m), int(d))
            except ValueError:
                raised = None
        items.append(
            Question(project=project, flag=flag, text=item_text, raised=raised)
        )
    return items


def _amber_age_h() -> float:
    try:
        return float(os.environ.get("METASPHERE_QUESTIONS_AMBER_AGE_H", "24"))
    except ValueError:
        return 24.0


def check_questions(paths: Paths, *, today: _dt.date | None = None) -> list[Question]:
    """Return QUESTIONS.md items DUE to remind the operator about.

    Eligibility (proposal §1): 🔴 always; 🟡 once aged past
    ``METASPHERE_QUESTIONS_AMBER_AGE_H`` (default 24h); 🟢 never (FYI —
    surfaces in the morning briefing, never interrupts the day).

    Pure: applies only the flag/aging gate. Work-hours, batching,
    cooldown and dedup live in :func:`heartbeat_once`.
    """
    p = _questions_file(paths)
    if not p.is_file():
        return []
    try:
        text = p.read_text(encoding="utf-8")
    except OSError:
        return []
    today = today or _dt.datetime.now(_dt.timezone.utc).date()
    amber_age = _amber_age_h()
    due: list[Question] = []
    for q in parse_questions(text):
        if q.flag == _RED:
            due.append(q)
        elif q.flag == _AMBER:
            age = q.age_hours(today)
            if age is None or age >= amber_age:
                due.append(q)
        # 🟢 never auto-pings.
    return due


def _format_questions_ping(due: list[Question]) -> str:
    """One batched message for all due items (never one ping per item)."""
    reds = [q for q in due if q.flag == _RED]
    ambers = [q for q in due if q.flag == _AMBER]
    head_bits = []
    if reds:
        head_bits.append(f"{len(reds)} blocking")
    if ambers:
        head_bits.append(f"{len(ambers)} soon")
    head = " · ".join(head_bits) or f"{len(due)} open"

    def _one(q: Question) -> str:
        proj = f"{q.project}: " if q.project else ""
        snippet = q.text.split(". ")[0].strip()
        if len(snippet) > 90:
            snippet = snippet[:87].rstrip() + "…"
        return f"{q.flag} {proj}{snippet}"

    lines = [_one(q) for q in reds + ambers]
    return "⏳ Needs from the operator — " + head + ":\n" + "\n".join(lines)


def _questions_enabled() -> bool:
    return os.environ.get("METASPHERE_QUESTIONS_ENABLED", "").strip().lower() in (
        "1",
        "true",
        "yes",
        "on",
    )


def _questions_in_work_hours(now: _dt.datetime | None = None) -> bool:
    """True if local time is within the reminder window (default 11–22).

    No-noise discipline (proposal §1a): reminders fire work-hours only.
    Timezone + bounds are env-overridable; failures fail-open to True so
    a missing tzdata never silently swallows a blocking-item reminder.
    """
    try:
        start = int(os.environ.get("METASPHERE_QUESTIONS_WORK_START", "11"))
        end = int(os.environ.get("METASPHERE_QUESTIONS_WORK_END", "22"))
    except ValueError:
        start, end = 11, 22
    tz_name = os.environ.get("METASPHERE_QUESTIONS_TZ", "Europe/Berlin")
    try:
        from zoneinfo import ZoneInfo

        now = now or _dt.datetime.now(ZoneInfo(tz_name))
        if now.tzinfo is None:
            now = now.replace(tzinfo=ZoneInfo(tz_name))
        else:
            now = now.astimezone(ZoneInfo(tz_name))
    except Exception:
        return True
    return start <= now.hour < end


def _cooldown_bucket(now: _dt.datetime | None = None) -> int:
    """Coarse window index so a stable due-set re-pings ≤ once per window."""
    try:
        cooldown = float(os.environ.get("METASPHERE_QUESTIONS_COOLDOWN_H", "6"))
    except ValueError:
        cooldown = 6.0
    cooldown = max(1.0, cooldown)
    now = now or _dt.datetime.now(_dt.timezone.utc)
    epoch_h = now.timestamp() / 3600.0
    return int(epoch_h // cooldown)


# ---------------------------------------------------------------------------
# Intake-drift safeguard — weekly-plan "Needs from the operator" → QUESTIONS.md
#
# Design: internal design notes §4. A lead-owned
# ``weekly-plan-<project>.md`` ``## Needs from the operator`` bullet is the INTAKE
# channel; the orchestrator promotes it into the canonical QUESTIONS.md so it
# reaches the briefing + heartbeat renderers. This detects items filed in a
# weekly-plan but never promoted — the leaf→canonical gap where a lead's
# blocking question can silently die — and surfaces them TO THE ORCHESTRATOR,
# never to the operator, as "intake pending."
#
# SHIPPED DISABLED BY DEFAULT behind ``METASPHERE_QUESTIONS_INTAKE_DRIFT_ENABLED``
# (proposal open-Q #4 — auto-diff vs the orchestrator's per-heartbeat glance).
# Off = rely on the glance (no extra message); on = auto-diff each tick. Both
# resolutions are preserved by the flag; flip the default once decided.
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class IntakeItem:
    """One ``## Needs from the operator`` bullet not yet in QUESTIONS.md."""

    project: str
    text: str

    def sig(self) -> str:
        import hashlib

        return hashlib.sha1(
            f"{self.project}|{self.text}".encode("utf-8")
        ).hexdigest()[:12]


def _norm_intake(text: str) -> str:
    """Loose match key: lowercased alnum run; flags, dates, punctuation dropped.

    The same item is usually reworded slightly when promoted into
    QUESTIONS.md (flag added, trailing date, a clarifying clause), so the
    match is intentionally fuzzy — exact equality would false-positive on
    every promoted item. Dropping non-alnum + dates leaves the content words.
    """
    import re

    t = re.sub(r"\(\d{4}-\d{2}-\d{2}\)", " ", text.lower())
    t = re.sub(r"[^a-z0-9]+", " ", t)
    return " ".join(t.split())


def parse_needs_from_operator(text: str) -> list[str]:
    """Bullets under a weekly-plan ``## Needs from the operator`` heading.

    The section runs from that heading to the next ``## `` heading. Each
    ``- `` bullet is one intake item; a leading 🔴/🟡/🟢 flag is stripped.
    The ``_(none currently recorded …)_`` placeholder is not a bullet, so
    it is naturally skipped.
    """
    items: list[str] = []
    in_section = False
    for raw in text.splitlines():
        stripped = raw.strip()
        if stripped.startswith("## "):
            heading = stripped[3:].strip().lower()
            in_section = heading.startswith("needs from the operator")
            continue
        if not in_section or not stripped.startswith("- "):
            continue
        body = stripped[2:].lstrip()
        if body[:1] in _QUESTION_FLAGS:
            body = body[1:].strip()
        if body:
            items.append(body)
    return items


def _weekly_plan_project(path: Path) -> str:
    prefix = "weekly-plan-"
    stem = path.stem
    return stem[len(prefix):] if stem.startswith(prefix) else stem


def check_intake_drift(paths: Paths) -> list[IntakeItem]:
    """Weekly-plan ``Needs from the operator`` items NOT yet in QUESTIONS.md.

    Pure detector: for each ``weekly-plan-*.md`` directly under
    ``paths.state`` (archived plans live in ``state/archive/`` and the glob
    is non-recursive, so they never alarm), diff its loose-normalised
    ``Needs from the operator`` bullets against the QUESTIONS.md questions.
    Anything unmatched is intake that was never promoted — returned for the
    orchestrator to triage. Never raises; degrades to ``[]`` on any I/O hiccup.
    """
    state = paths.state
    if not state.is_dir():
        return []
    promoted: set[str] = set()
    q_file = _questions_file(paths)
    if q_file.is_file():
        try:
            promoted = {
                _norm_intake(q.text)
                for q in parse_questions(q_file.read_text(encoding="utf-8"))
            }
        except OSError:
            promoted = set()
    drift: list[IntakeItem] = []
    seen: set[tuple[str, str]] = set()
    for plan in sorted(state.glob("weekly-plan-*.md")):
        project = _weekly_plan_project(plan)
        try:
            text = plan.read_text(encoding="utf-8")
        except OSError:
            continue
        for item in parse_needs_from_operator(text):
            norm = _norm_intake(item)
            if not norm or norm in promoted:
                continue
            dedup = (project, norm)
            if dedup in seen:
                continue
            seen.add(dedup)
            drift.append(IntakeItem(project=project, text=item))
    return drift


def _format_intake_drift(items: list[IntakeItem]) -> str:
    """One batched message for the orchestrator (never one per item)."""
    lines = []
    for it in items:
        proj = f"{it.project}: " if it.project else ""
        snippet = it.text.strip()
        if len(snippet) > 100:
            snippet = snippet[:97].rstrip() + "…"
        lines.append(f"- {proj}{snippet}")
    head = (
        f"📥 Intake pending — {len(items)} weekly-plan "
        "'Needs from the operator' item(s) not yet promoted to QUESTIONS.md:"
    )
    return head + "\n" + "\n".join(lines)


def _intake_drift_enabled() -> bool:
    return os.environ.get(
        "METASPHERE_QUESTIONS_INTAKE_DRIFT_ENABLED", ""
    ).strip().lower() in ("1", "true", "yes", "on")


# ---------------------------------------------------------------------------
# Agent context + invocation
# ---------------------------------------------------------------------------


def build_agent_context(agent: str = "@orchestrator", paths: Paths | None = None) -> str:
    """Build the heartbeat context block for ``agent``.

    Delegates the bulk to :func:`metasphere.context.build_context` and
    prepends a ``# HEARTBEAT`` header so the receiving agent recognises
    this as a periodic tick rather than a fresh prompt.
    """
    paths = paths or resolve()
    body = build_context(paths)
    header = (
        f"# HEARTBEAT {_utcnow()} ({agent})\n\n"
        "## Response contract\n\n"
        "If this tick produced no user-visible work, change, warning, or "
        "question, reply with exactly `[idle]` and no other text. Do not "
        "describe an empty queue, say that nothing is new, or say that you "
        "are standing by. Do not repeat a warning or status merely because "
        "it appears in Recent Events or was reported on an earlier tick. "
        "Only send prose when the operator should actually receive a new "
        "Telegram message.\n"
    )
    return header + "\n" + body


def invoke_agent_heartbeat(
    agent: str = "@orchestrator",
    paths: Paths | None = None,
) -> bool:
    """Submit the heartbeat context to ``agent``.

    If a tmux session for the agent is alive, paste via the
    ``submit_to_tmux`` helper. Otherwise fall back to a
    provider-specific headless one-shot. Returns True on best-effort success.
    """
    paths = paths or resolve()
    context = build_agent_context(agent, paths)
    # Project-scoped agents live in ``metasphere-<project>-<agent>``
    # sessions; ``session_name_for`` alone misses these.
    # ``_resolve_session`` walks the agent registry and returns the
    # project-aware name, falling back to the bare form
    # for ephemerals not in the registry. Lazy-imported to avoid the
    # session→heartbeat circular at module import.
    from .session import _resolve_session
    session = _resolve_session(agent)

    if session_alive(session):
        from .tmux import submit_to_tmux as _tmux_submit

        # Probe pane for rate-limit signals before injection so we can
        # rotate credentials while the orchestrator is still idle.
        try:
            from .cli.failsafe import probe_and_rotate
            probe_and_rotate(session, paths, agent=agent)
        except Exception:
            pass

        # defer_if_busy=True: if the input box shows typing (a human
        # is mid-keystroke), skip this tick — the next heartbeat will
        # retry. Prevents the 2026-04-16 "heartbeat took over my
        # cursor" interleaving.
        # escape_prefix=False: heartbeats must never interrupt a
        # running tool call. Paste+Enter without the Escape-prefix;
        # Claude Code queues the keystrokes and processes them when
        # the current tool finishes. "Only user-inbound interrupts"
        # (operator-confirmed 2026-04-16).
        ok = _tmux_submit(
            session, context, defer_if_busy=True, escape_prefix=False
        )
        if not ok:
            return False
        # Heartbeat tick is one of the four signals reap_dormant uses
        # to confirm the agent is alive. Refresh on successful inject.
        touch_last_active(agent, paths)
        try:
            log_event(
                "heartbeat.invoke",
                f"injected heartbeat into {session}",
                agent=agent,
                paths=paths,
            )
        except Exception:
            pass
        return True

    # Fallback: provider-specific one-shot with sandboxed tools.
    agent_dir = paths.agent_dir(agent)
    sandbox = "none"
    sf = agent_dir / "sandbox"
    if sf.is_file():
        try:
            sandbox = sf.read_text(encoding="utf-8").strip() or "none"
        except OSError:
            pass
    allowed = "Read,Write,Edit,Bash,Glob,Grep"
    if sandbox == "readonly":
        allowed = "Read,Glob,Grep"
    elif sandbox == "nobash":
        allowed = "Read,Write,Edit,Glob,Grep"

    # Match bash: cd to the agent's scope dir before invoking the runtime so
    # `git rev-parse --show-toplevel` (and metasphere.paths.resolve()
    # inside the spawned process) resolve relative to the agent's repo,
    # not whatever cwd the heartbeat daemon was started from.
    scope_cwd: str | None = None
    scope_file = agent_dir / "scope"
    if scope_file.is_file():
        try:
            v = scope_file.read_text(encoding="utf-8").strip()
            if v and Path(v).is_dir():
                scope_cwd = v
        except OSError:
            pass

    # Mark the one-shot as a metasphere-managed, non-interactive session
    # (same marker the gateway tmux respawn loop and ephemeral spawns set)
    # so the UserPromptSubmit context hook still injects this agent's
    # context. Without it the hook would misread the headless heartbeat as
    # an interactive human session and skip injection (issue #150).
    env = {**os.environ, "METASPHERE_GATEWAY_SESSION": "1"}
    runtime = os.environ.get("METASPHERE_AGENT_RUNTIME", "claude").strip().lower()
    if runtime == "claude":
        cmd = ["claude", "-p", "--allowedTools", allowed]
    elif runtime == "codex":
        # Codex has no direct equivalent of Claude's per-tool ``nobash``
        # allowlist. Fail closed instead of silently granting shell access.
        if sandbox == "nobash":
            return False
        sandbox_mode = "read-only" if sandbox == "readonly" else "workspace-write"
        cmd = [
            "codex",
            "--ask-for-approval",
            "never",
            "exec",
            "--sandbox",
            sandbox_mode,
            "--skip-git-repo-check",
            "-",
        ]
    else:
        return False
    try:
        subprocess.run(
            cmd,
            input=context,
            text=True,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
            cwd=scope_cwd,
            env=env,
        )
    except Exception:
        return False
    touch_last_active(agent, paths)
    try:
        log_event(
            "heartbeat.invoke",
            f"one-shot heartbeat to {agent}",
            agent=agent,
            paths=paths,
        )
    except Exception:
        pass
    return True


# ---------------------------------------------------------------------------
# Tick + daemon
# ---------------------------------------------------------------------------


def log_status_to_disk(paths: Paths) -> None:
    """Record ``alive at <iso ts>`` to ``state/heartbeat_last_run``."""
    p = _last_run_file(paths)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"alive at {_utcnow()}\n", encoding="utf-8")


def heartbeat_once(paths: Paths | None = None, invoke_agent: bool = False) -> None:
    """Run one heartbeat tick: scan, dedupe-notify, optionally invoke.

    The heartbeat daemon always scans the *whole repo* regardless of the
    cwd it was started from. ``paths.scope`` is normalised to
    ``paths.project_root`` so a daemon launched from a nested cwd (or with
    ``METASPHERE_SCOPE`` set) doesn't under-report urgent items in
    sibling scopes.
    """
    paths = paths or resolve()
    if paths.scope != paths.project_root:
        paths = dataclasses.replace(paths, scope=paths.project_root)

    new_urgent: list[Message] = []
    for m in check_urgent_messages(paths):
        key = f"urgent:{m.id}"
        if not already_notified(paths, key):
            mark_notified(paths, key)
            new_urgent.append(m)

    new_blocked: list[AgentRecord] = []
    for a in check_blocked_agents(paths):
        key = f"status:{a.name}:{a.status}"
        if not already_notified(paths, key):
            mark_notified(paths, key)
            new_blocked.append(a)

    urgent_tasks, total_tasks = check_urgent_tasks(paths)
    if urgent_tasks > 0:
        key = f"tasks:urgent:{urgent_tasks}"
        if not already_notified(paths, key):
            mark_notified(paths, key)
            try:
                log_event(
                    "heartbeat.tasks",
                    f"{urgent_tasks} urgent task(s) pending ({total_tasks} total)",
                    paths=paths,
                )
            except Exception:
                pass
            _notify_user(
                f"[heartbeat] {urgent_tasks} urgent task(s) pending ({total_tasks} total)",
                paths,
            )

    for m in new_urgent:
        try:
            log_event(
                "heartbeat.urgent_message",
                f"urgent message {m.id} from {m.from_}",
                meta={"msg_id": m.id, "from": m.from_},
                paths=paths,
            )
        except Exception:
            pass
        _notify_user(
            f"[URGENT] message from {m.from_}\n{(m.body or '').strip()[:500]}",
            paths,
        )
    for a in new_blocked:
        try:
            log_event(
                "heartbeat.blocked_agent",
                f"{a.name} {a.status}",
                agent=a.name,
                paths=paths,
            )
        except Exception:
            pass
        _notify_user(f"[heartbeat] agent {a.name} {a.status}", paths)

    # QUESTIONS.md reminder (opt-in; see check_questions). Batched into ONE
    # message; deduped per (due-set, cooldown-window) so a stable set
    # re-pings at most once per window, but a newly-blocking item (the set
    # changes) surfaces promptly.
    if _questions_enabled() and _questions_in_work_hours():
        due = check_questions(paths)
        if due:
            import hashlib

            set_sig = hashlib.sha1(
                "|".join(sorted(q.sig() for q in due)).encode("utf-8")
            ).hexdigest()[:12]
            key = f"questions:{set_sig}:{_cooldown_bucket()}"
            if not already_notified(paths, key):
                mark_notified(paths, key)
                try:
                    log_event(
                        "heartbeat.questions",
                        f"{len(due)} item(s) need the operator",
                        paths=paths,
                    )
                except Exception:
                    pass
                _notify_user(_format_questions_ping(due), paths)

    # Intake-drift safeguard (opt-in; see check_intake_drift). Surfaces
    # un-promoted weekly-plan "Needs from the operator" items to @orchestrator —
    # NOT the operator — as an !info, deduped per (drift-set, cooldown-window) so a
    # stable backlog nags at most once per window. wake=False: this is a
    # next-glance hint, never a spawn trigger.
    if _intake_drift_enabled():
        drift = check_intake_drift(paths)
        if drift:
            import hashlib

            drift_sig = hashlib.sha1(
                "|".join(sorted(it.sig() for it in drift)).encode("utf-8")
            ).hexdigest()[:12]
            key = f"intake_drift:{drift_sig}:{_cooldown_bucket()}"
            if not already_notified(paths, key):
                mark_notified(paths, key)
                try:
                    log_event(
                        "heartbeat.intake_drift",
                        f"{len(drift)} un-promoted intake item(s)",
                        paths=paths,
                    )
                except Exception:
                    pass
                try:
                    send_message(
                        "@orchestrator",
                        "!info",
                        _format_intake_drift(drift),
                        "@heartbeat",
                        paths,
                        wake=False,
                    )
                except Exception:
                    pass

    log_status_to_disk(paths)

    if invoke_agent:
        try:
            invoke_agent_heartbeat("@orchestrator", paths)
        except Exception:
            pass


def heartbeat_daemon(
    paths: Paths | None = None,
    interval_seconds: int = 300,
    invoke_agent: bool = False,
) -> None:
    """Run :func:`heartbeat_once` forever on ``interval_seconds`` cadence.

    Telegram long-poll used to be optionally bolted on here via a
    ``with_telegram_poll`` thread. That was a cutover-era convenience
    and a third polling path in the codebase. Removed: the gateway
    daemon (``metasphere-gateway``) is now the single source of truth
    for Telegram polling.
    """
    paths = paths or resolve()

    while True:
        try:
            heartbeat_once(paths, invoke_agent=invoke_agent)
        except Exception:
            # Daemon must not die on a single tick error.
            pass
        time.sleep(interval_seconds)
