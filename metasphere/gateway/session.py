"""Persistent ``@orchestrator`` tmux+REPL session lifecycle.

Mirrors the session-management half of ``scripts/metasphere-gateway``.
The session is named ``metasphere-orchestrator`` (note: NOT
``metasphere-@orchestrator`` — the gateway predates the agent-naming
convention used by :mod:`metasphere.agents` and the bash gateway has
historically used the bare name. We preserve that for compatibility.)
"""

from __future__ import annotations

import shutil
import subprocess
import time
from typing import Tuple

from ..events import log_event
from ..io import atomic_write_text
from ..paths import Paths, resolve

SESSION_NAME = "metasphere-orchestrator"

# The respawn loop the bash gateway puts in the pane. When the agent runs
# /exit, claude returns to bash, the loop sleeps, and a fresh REPL starts —
# picking up the latest harness automatically.
_RESPAWN_CMD = (
    "exec bash -c 'while true; do claude --dangerously-skip-permissions; "
    'ec=$?; echo "[gateway] claude exited ($ec), respawning in 1s..."; '
    "sleep 1; done'"
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
    return _tmux("has-session", "-t", name).returncode == 0


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

    r = _tmux("new-session", "-d", "-s", SESSION_NAME, "-c", scope_str)
    if r.returncode != 0:
        return False
    _tmux("set-option", "-t", SESSION_NAME, "mouse", "on")
    _tmux("set-option", "-t", SESSION_NAME, "history-limit", "100000")
    _tmux("send-keys", "-t", SESSION_NAME, _RESPAWN_CMD, "Enter")

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


def restart_session(reason: str, paths: Paths | None = None) -> None:
    """Restart claude inside the existing orchestrator session.

    Sends C-c twice + ``/exit`` + sleep, then re-launches the respawn
    loop. Leaves the tmux session itself intact so external attachers
    (mosh, spot-chat) don't drop. Mirrors ``restart_claude_in_session``.
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

    _tmux("send-keys", "-t", SESSION_NAME, "C-c")
    time.sleep(0.3)
    _tmux("send-keys", "-t", SESSION_NAME, "C-c")
    time.sleep(0.3)
    _tmux("send-keys", "-t", SESSION_NAME, "/exit", "Enter")
    time.sleep(1)
    _tmux("send-keys", "-t", SESSION_NAME, _RESPAWN_CMD, "Enter")
    time.sleep(0.5)


def ensure_session(paths: Paths | None = None) -> None:
    """Start the session if it isn't alive. Idempotent."""
    paths = paths or resolve()
    if not session_alive(SESSION_NAME):
        start_session(paths)
