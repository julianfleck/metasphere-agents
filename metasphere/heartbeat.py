"""Proactive monitoring daemon — Python port of scripts/metasphere-heartbeat.

Walks the repo for urgent unread messages, agents in waiting/blocked
states, and urgent tasks; optionally invokes the orchestrator agent
with a freshly built context block (via tmux paste if a session is
live, otherwise via a ``claude -p`` one-shot).

State (which urgent items have already been notified about) lives in
``$METASPHERE_DIR/state/heartbeat_state`` and is mutated under
``metasphere.io.file_lock`` so concurrent ticks cannot tear lines.

Stdlib only. The bash version stays in place during cutover; this
module must remain feature-equivalent until the systemd unit is
flipped to ``python -m metasphere.cli.heartbeat``.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import os
import shlex
import subprocess
import time
from pathlib import Path

from .agents import AgentRecord, list_agents, session_alive, session_name_for
from .context import build_context
from .events import log_event
from .io import file_lock
from .messages import Message, STATUS_UNREAD, collect_inbox
from .paths import Paths, resolve
from .tasks import list_tasks


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _notify_user(text: str, paths: Paths) -> None:
    """Send a heartbeat-class notification to the user via Telegram.

    Mirrors the bash ``notify()`` helper. Failures are swallowed —
    heartbeat ticks must never raise. Looks up the chat id via the
    posthook resolver so the heartbeat and posthook share the same
    config-file precedence.
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
    msgs = collect_inbox(paths.scope, paths.repo)
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
    items = list_tasks(paths.scope, paths.repo, include_completed=False)
    urgent = sum(1 for t in items if t.priority == "!urgent")
    return urgent, len(items)


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
    header = f"# HEARTBEAT {_utcnow()} ({agent})\n"
    return header + "\n" + body


def invoke_agent_heartbeat(
    agent: str = "@orchestrator",
    paths: Paths | None = None,
) -> bool:
    """Submit the heartbeat context to ``agent``.

    If a tmux session for the agent is alive, paste via the bash
    ``submit_to_tmux`` helper (invariant 15). Otherwise fall back to a
    ``claude -p`` one-shot. Returns True on best-effort success.
    """
    paths = paths or resolve()
    context = build_agent_context(agent, paths)
    session = session_name_for(agent)

    if session_alive(session):
        submit_script = paths.repo / "scripts" / "metasphere-tmux-submit"
        if not submit_script.is_file():
            return False
        # Hard-codes the bash function's two-positional-arg signature
        # (session, context). If scripts/metasphere-tmux-submit ever
        # changes submit_to_tmux's argument shape this will silently
        # break — keep this Python wrapper in sync with that file.
        cmd = (
            f"source {shlex.quote(str(submit_script))}; "
            f"submit_to_tmux \"$1\" \"$2\""
        )
        try:
            subprocess.run(
                ["bash", "-c", cmd, "_", session, context],
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=30,
            )
        except Exception:
            return False
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

    # Fallback: one-shot claude -p with sandboxed allowed tools.
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

    # Match bash: cd to the agent's scope dir before invoking claude so
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

    try:
        subprocess.run(
            ["claude", "-p", "--allowedTools", allowed],
            input=context,
            text=True,
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=60,
            cwd=scope_cwd,
        )
    except Exception:
        return False
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
    cwd it was started from. The bash version uses ``find "$REPO_ROOT"``;
    the Python equivalent normalises ``paths.scope`` to ``paths.repo``
    here so a daemon launched from a nested cwd (or with
    ``METASPHERE_SCOPE`` set) doesn't under-report urgent items in
    sibling scopes.
    """
    paths = paths or resolve()
    if paths.scope != paths.repo:
        paths = dataclasses.replace(paths, scope=paths.repo)

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

    log_status_to_disk(paths)

    if invoke_agent:
        try:
            invoke_agent_heartbeat("@orchestrator", paths)
        except Exception:
            pass


def heartbeat_daemon(
    paths: Paths | None = None,
    interval_seconds: int = 30,
    invoke_agent: bool = False,
    with_telegram_poll: bool = False,
) -> None:
    """Run :func:`heartbeat_once` forever on ``interval_seconds`` cadence.

    The bash daemon historically did double duty: heartbeat ticks plus
    Telegram inbound long-polling on a 5s cadence. The Python port
    prefers single-responsibility daemons (run
    ``python -m metasphere.cli.telegram poll`` from a sibling unit), but
    callers can opt into the combined behaviour with
    ``with_telegram_poll=True`` to ease cutover from a single systemd
    unit.
    """
    paths = paths or resolve()

    if with_telegram_poll:
        import threading

        def _poll_loop() -> None:
            try:
                from .telegram import poller as _poller
                from .cli.telegram import _handle_update
            except Exception:
                return
            while True:
                try:
                    offset = _poller.load_offset()
                    updates = _poller.get_updates(offset=offset, timeout=1)
                    for u in updates:
                        try:
                            _handle_update(u)
                        except Exception:
                            pass
                        _poller.save_offset(u.update_id + 1)
                except Exception:
                    pass
                time.sleep(5)

        threading.Thread(target=_poll_loop, name="telegram-poll", daemon=True).start()

    while True:
        try:
            heartbeat_once(paths, invoke_agent=invoke_agent)
        except Exception:
            # Daemon must not die on a single tick error.
            pass
        time.sleep(interval_seconds)
