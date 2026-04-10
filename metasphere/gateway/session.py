"""Persistent ``@orchestrator`` tmux+REPL session lifecycle.

The session is named ``metasphere-orchestrator`` (note: NOT
``metasphere-@orchestrator`` — the gateway predates the agent-naming
convention used by :mod:`metasphere.agents` and has historically used
the bare name. Preserved for compatibility.)
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
    """Snapshot the current harness content hash so the per-turn context
    hook can detect drift and surface a reload warning to the agent.
    Best-effort; never raises."""
    try:
        paths.state.mkdir(parents=True, exist_ok=True)
        atomic_write_text(paths.state / "harness_hash_baseline", harness_hash(paths) + "\n")
    except OSError:
        pass

SESSION_NAME = "metasphere-orchestrator"

# Build the respawn loop command for a given agent. The loop writes a
# per-agent restart_pending marker whenever claude exits, so the watchdog
# can inject a continuation prompt into the fresh instance.
def _respawn_cmd(agent: str = "@orchestrator") -> str:
    """Return the respawn loop command for a given agent.

    The marker is a JSON file with timestamp + reason + agent. If
    restart_session() already wrote one (programmatic restart), the loop
    overwrites it with a fresh timestamp — harmless, and ensures the
    grace period resets to when the new process actually starts.
    """
    safe_agent = agent.replace("'", "")  # paranoia
    return (
        "exec bash -c '"
        'STATE_DIR="$HOME/.metasphere/state"; '
        "mkdir -p \"$STATE_DIR\"; "
        "while true; do "
        # Refresh METASPHERE_REPO_ROOT from git on each restart so
        # stale env vars from a previous session don't persist.
        'export METASPHERE_REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo "$METASPHERE_REPO_ROOT")"; '
        "claude --dangerously-skip-permissions; "
        'ec=$?; echo "[gateway] claude exited ($ec), respawning in 1s..."; '
        'echo "{\\"timestamp\\": '
        "$(date +%s)"
        ', \\"reason\\": \\"claude exited (code $ec)\\"'
        f', \\"agent\\": \\"{safe_agent}\\"'
        '}" '
        f'> "$STATE_DIR/restart_pending.{safe_agent}.json"; '
        "sleep 1; "
        "done'"
    )


# Backward-compat alias — the orchestrator's loop.
_RESPAWN_CMD = _respawn_cmd("@orchestrator")


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
    # Delegate to metasphere.agents.session_alive so a future fix to one
    # path doesn't silently desync the other.
    return _agents_session_alive(name)


def session_health(paths: Paths | None = None) -> Tuple[bool, int]:
    """Return ``(alive, idle_seconds_since_session_activity)``.

    ``idle_seconds`` is 0 when the session is dead or activity cannot be
    parsed (an unparseable activity is treated as "fine" — the watchdog
    only acts on stuck-prompt patterns, never on idle time alone).
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
    returns True without touching it. Does not inject initial context —
    claude-code auto-loads ``CLAUDE.md`` from the repo root, so we
    deliberately do not paste any bootstrap text into the pane.
    """
    paths = paths or resolve()
    if session_alive(SESSION_NAME):
        return True

    scope_file = paths.agents / "@orchestrator" / "scope"
    try:
        scope_str = scope_file.read_text(encoding="utf-8").strip() or str(paths.repo)
    except (OSError, FileNotFoundError):
        scope_str = str(paths.repo)
    # If the configured scope is gone, fall back to repo root with a
    # log_event so the failure is debuggable instead of tmux failing
    # silently with `-c <bad-path>`.
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
    # Set METASPHERE_REPO_ROOT explicitly so it matches the repo we're in,
    # regardless of what the parent process had in its env.
    _tmux("send-keys", "-t", SESSION_NAME,
          f"export METASPHERE_REPO_ROOT={scope_str}", "Enter")
    _tmux("send-keys", "-t", SESSION_NAME, _RESPAWN_CMD, "Enter")

    # Write restart marker so watchdog injects a wake-up prompt into the
    # fresh instance (same path as restart_session — new sessions need a
    # kick too).
    _write_restart_pending(paths, "session created", agent="@orchestrator")

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


def _restart_marker_path(paths: Paths, agent: str = "@orchestrator") -> Path:
    """Return the per-agent restart marker path."""
    # Normalize: ensure @ prefix, use it in filename
    if not agent.startswith("@"):
        agent = "@" + agent
    return paths.state / f"restart_pending.{agent}.json"


def _write_restart_pending(paths: Paths, reason: str, agent: str = "@orchestrator") -> None:
    """Write a restart-pending marker so the watchdog knows to inject a
    continuation prompt once the fresh Claude instance is ready."""
    try:
        paths.state.mkdir(parents=True, exist_ok=True)
        atomic_write_text(
            _restart_marker_path(paths, agent),
            json.dumps({
                "timestamp": int(time.time()),
                "reason": reason,
                "agent": agent,
            }) + "\n",
        )
    except OSError:
        pass


def restart_agent_session(
    agent: str,
    reason: str,
    session_name: str | None = None,
    paths: Paths | None = None,
) -> bool:
    """Restart claude inside any agent's tmux session.

    Sends C-c twice + ``/exit``, then the respawn loop (already running
    in the pane's shell) revives Claude automatically. A per-agent
    restart-pending marker is written so the watchdog can inject a
    continuation prompt once the new instance is ready.

    Returns True if the restart was initiated.
    """
    paths = paths or resolve()
    target = session_name or SESSION_NAME
    if not session_alive(target):
        try:
            log_event(
                "supervisor.restart_claude",
                f"session {target} not alive, skipped: {reason}",
                agent="@daemon-supervisor",
                paths=paths,
            )
        except Exception:
            pass
        return False

    try:
        log_event(
            "supervisor.restart_claude",
            f"{agent} restart: {reason}",
            agent="@daemon-supervisor",
            paths=paths,
        )
    except Exception:
        pass

    # Write marker BEFORE killing the process so the watchdog can
    # detect the restart even if this function is interrupted.
    _write_restart_pending(paths, reason, agent=agent)

    _tmux("send-keys", "-t", target, "C-c")
    time.sleep(0.3)
    _tmux("send-keys", "-t", target, "C-c")
    time.sleep(0.3)
    _tmux("send-keys", "-t", target, "/exit", "Enter")
    # The respawn loop (already running in the pane shell) handles
    # restarting Claude. We do NOT re-send the respawn command — that
    # would nest a second loop inside the first.
    return True


def restart_session(reason: str, paths: Paths | None = None) -> None:
    """Restart the orchestrator session. Backward-compat wrapper."""
    restart_agent_session("@orchestrator", reason, SESSION_NAME, paths)


def ensure_session(paths: Paths | None = None) -> None:
    """Start the session if it isn't alive. Idempotent."""
    paths = paths or resolve()
    if not session_alive(SESSION_NAME):
        start_session(paths)
