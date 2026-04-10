"""Persistent ``@orchestrator`` tmux+REPL session lifecycle.

Mirrors the session-management half of ``scripts/metasphere-gateway``.
The session is named ``metasphere-orchestrator`` (note: NOT
``metasphere-@orchestrator`` — the gateway predates the agent-naming
convention used by :mod:`metasphere.agents` and the bash gateway has
historically used the bare name. We preserve that for compatibility.)
"""

from __future__ import annotations

import json
import shutil
import subprocess
import time
from pathlib import Path
from typing import Tuple

from ..agents import session_alive as _agents_session_alive
from ..context import harness_hash
from ..events import log_event
from ..io import atomic_write_text
from ..paths import Paths, resolve


def write_harness_hash_baseline(paths: Paths) -> None:
    """Mirror the bash ``write_harness_hash_baseline``: snapshot the
    current harness content hash so the per-turn context hook can detect
    drift and surface a reload warning to the agent. Best-effort; never
    raises."""
    try:
        paths.state.mkdir(parents=True, exist_ok=True)
        atomic_write_text(paths.state / "harness_hash_baseline", harness_hash(paths) + "\n")
    except OSError:
        pass

SESSION_NAME = "metasphere-orchestrator"

# The respawn loop the bash gateway puts in the pane. When the agent runs
# /exit, claude returns to bash, the loop sleeps, and a fresh REPL starts —
# picking up the latest harness automatically.
# The respawn loop writes a restart_pending marker whenever claude exits,
# so the watchdog can inject a continuation prompt into the fresh instance.
# The marker is a JSON file with timestamp + reason. If restart_session()
# already wrote one (programmatic restart), the loop overwrites it with a
# fresh timestamp — harmless, and ensures the grace period resets to when
# the new process actually starts.
_RESPAWN_CMD = (
    "exec bash -c '"
    'STATE_DIR="$HOME/.metasphere/state"; '
    "mkdir -p \"$STATE_DIR\"; "
    "while true; do "
    "claude --dangerously-skip-permissions; "
    'ec=$?; echo "[gateway] claude exited ($ec), respawning in 1s..."; '
    'echo "{\\\"timestamp\\\": $(date +%s), \\\"reason\\\": \\\"claude exited (code $ec)\\\"}" '
    '> "$STATE_DIR/restart_pending.json"; '
    "sleep 1; "
    "done'"
)


def _tmux_bin() -> str:
    return shutil.which("tmux") or "tmux"


def _tmux(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_tmux_bin(), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def session_alive(name: str = SESSION_NAME) -> bool:
    # Delegate to the canonical metasphere.agents.session_alive so a future
    # fix to one path doesn't silently desync the other (M2, wave-4 review).
    return _agents_session_alive(name)


def session_health(paths: Paths | None = None) -> Tuple[bool, int]:
    """Return ``(alive, idle_seconds_since_session_activity)``.

    ``idle_seconds`` is 0 when the session is dead or activity cannot be
    parsed (the bash version treats an unparseable activity as "fine" and
    we mirror that — the watchdog only acts on stuck-prompt patterns,
    never on idle time alone).
    """
    if not session_alive(SESSION_NAME):
        return (False, 0)
    r = _tmux("display-message", "-t", SESSION_NAME, "-p", "#{session_activity}")
    if r.returncode != 0 or not r.stdout.strip():
        return (True, 0)
    try:
        activity = int(r.stdout.strip())
    except ValueError:
        return (True, 0)
    return (True, max(0, int(time.time()) - activity))


def start_session(paths: Paths | None = None) -> bool:
    """Create the orchestrator tmux session and start the claude respawn loop.

    Returns True on success. Idempotent: if the session already exists,
    returns True without touching it. Mirrors the bash ``start_session``
    minus the (no-op) initial-context injection — claude-code auto-loads
    ``CLAUDE.md`` from the repo root, so we deliberately do not paste any
    bootstrap text into the pane.
    """
    paths = paths or resolve()
    if session_alive(SESSION_NAME):
        return True

    scope_file = paths.agents / "@orchestrator" / "scope"
    try:
        scope_str = scope_file.read_text(encoding="utf-8").strip() or str(paths.repo)
    except (OSError, FileNotFoundError):
        scope_str = str(paths.repo)
    # L1 (wave-4 review): if the configured scope is gone, fall back to
    # repo root with a log_event so the failure is debuggable instead of
    # tmux failing silently with `-c <bad-path>`.
    if not Path(scope_str).is_dir():
        try:
            log_event(
                "agent.session",
                f"configured scope missing, falling back to repo: {scope_str}",
                agent="@orchestrator",
                paths=paths,
            )
        except Exception:
            pass
        scope_str = str(paths.repo)

    r = _tmux("new-session", "-d", "-s", SESSION_NAME, "-c", scope_str)
    if r.returncode != 0:
        return False
    _tmux("set-option", "-t", SESSION_NAME, "mouse", "on")
    _tmux("set-option", "-t", SESSION_NAME, "history-limit", "100000")
    _tmux("send-keys", "-t", SESSION_NAME, _RESPAWN_CMD, "Enter")

    # Write restart marker so watchdog injects a wake-up prompt into the
    # fresh instance (same path as restart_session — new sessions need a
    # kick too).
    _write_restart_pending(paths, "session created")

    # Snapshot harness hash so drift detection has a reference point.
    write_harness_hash_baseline(paths)

    # Status marker for the agent dir (best-effort).
    try:
        agent_dir = paths.agents / "@orchestrator"
        agent_dir.mkdir(parents=True, exist_ok=True)
        atomic_write_text(agent_dir / "status", "active: persistent session\n")
    except OSError:
        pass

    try:
        log_event(
            "agent.session",
            "Orchestrator session started",
            agent="@orchestrator",
            paths=paths,
        )
    except Exception:
        pass
    return True


def _write_restart_pending(paths: Paths, reason: str) -> None:
    """Write a restart-pending marker so the watchdog knows to inject a
    continuation prompt once the fresh Claude instance is ready."""
    try:
        paths.state.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            paths.state / "restart_pending.json",
            json.dumps({
                "timestamp": int(time.time()),
                "reason": reason,
            }) + "\n",
        )
    except OSError:
        pass


def restart_session(reason: str, paths: Paths | None = None) -> None:
    """Restart claude inside the existing orchestrator session.

    Sends C-c twice + ``/exit``, then the respawn loop (already running
    in the pane's shell) revives Claude automatically. A restart-pending
    marker is written so the watchdog can inject a continuation prompt
    once the new instance is ready. Leaves the tmux session itself
    intact so external attachers (mosh, spot-chat) don't drop.
    """
    paths = paths or resolve()
    if not session_alive(SESSION_NAME):
        # Nothing to restart — let the caller decide whether to start_session.
        try:
            log_event(
                "supervisor.restart_claude",
                f"session not alive, skipped: {reason}",
                agent="@daemon-supervisor",
                paths=paths,
            )
        except Exception:
            pass
        return

    try:
        log_event(
            "supervisor.restart_claude",
            reason,
            agent="@daemon-supervisor",
            paths=paths,
        )
    except Exception:
        pass

    # Write marker BEFORE killing the process so the watchdog can
    # detect the restart even if this function is interrupted.
    _write_restart_pending(paths, reason)

    _tmux("send-keys", "-t", SESSION_NAME, "C-c")
    time.sleep(0.3)
    _tmux("send-keys", "-t", SESSION_NAME, "C-c")
    time.sleep(0.3)
    _tmux("send-keys", "-t", SESSION_NAME, "/exit", "Enter")
    # The respawn loop (already running in the pane shell) handles
    # restarting Claude. We do NOT re-send _RESPAWN_CMD — that would
    # nest a second loop inside the first.


def ensure_session(paths: Paths | None = None) -> None:
    """Start the session if it isn't alive. Idempotent."""
    paths = paths or resolve()
    if not session_alive(SESSION_NAME):
        start_session(paths)
