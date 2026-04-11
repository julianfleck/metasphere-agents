"""tmux session lifecycle.

Canonical module for managing agent sessions: list, start, stop,
restart, send, attach. All agent types use this — the gateway module
delegates here for the orchestrator, and ``agents.wake_persistent``
handles the initial bring-up sequence.
"""

from __future__ import annotations

import os
import subprocess
import time
from dataclasses import dataclass
from typing import Optional

from .agents import (
    AgentRecord,
    _tmux_bin,
    list_agents,
    session_alive,
    session_name_for,
)
from .events import log_event
from .paths import Paths, resolve

_SESSION_PREFIX = "metasphere-"
VIEWER_SESSION_NAME = "metasphere-all"


@dataclass
class SessionInfo:
    name: str
    agent: str  # @name (with leading @)
    windows: int
    created: str
    attached: bool


def _list_sessions_raw() -> list[str]:
    try:
        out = subprocess.run(
            [_tmux_bin(), "list-sessions", "-F",
             "#{session_name}\t#{session_windows}\t#{session_created}\t#{session_attached}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
        )
        if out.returncode != 0:
            return []
        return [l for l in out.stdout.splitlines() if l.strip()]
    except FileNotFoundError:
        return []


def list_sessions() -> list[SessionInfo]:
    """Return all live ``metasphere-*`` tmux sessions."""
    sessions: list[SessionInfo] = []
    for line in _list_sessions_raw():
        parts = line.split("\t")
        if len(parts) < 4:
            continue
        name, windows, created, attached = parts[0], parts[1], parts[2], parts[3]
        if not name.startswith(_SESSION_PREFIX):
            continue
        sessions.append(SessionInfo(
            name=name,
            agent="@" + name[len(_SESSION_PREFIX):],
            windows=int(windows or 0),
            created=created,
            attached=attached == "1",
        ))
    return sessions


def session_info(name_or_agent: str) -> Optional[SessionInfo]:
    """Look up a single session by tmux name or @agent id."""
    target = name_or_agent
    if target.startswith("@"):
        target = session_name_for(target)
    for s in list_sessions():
        if s.name == target:
            return s
    return None


def attach_to(name_or_agent: str) -> int:
    """Exec ``tmux attach`` to the named session. Replaces current proc.

    Returns 1 if no such session (does not exec).
    """
    target = name_or_agent
    if target.startswith("@"):
        target = session_name_for(target)
    if not session_alive(target):
        return 1
    os.execvp(_tmux_bin(), [_tmux_bin(), "attach-session", "-t", target])
    return 0  # unreachable


# ---------------------------------------------------------------------------
# Lifecycle: stop, restart, send
# ---------------------------------------------------------------------------

def _tmux_run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_tmux_bin(), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def _resolve_session(agent: str) -> str:
    """Resolve agent name to tmux session name."""
    if not agent.startswith("@"):
        agent = "@" + agent
    # Orchestrator uses the historical name (no @ prefix in session).
    from .gateway.session import SESSION_NAME

    if agent == "@orchestrator":
        return SESSION_NAME
    return session_name_for(agent)


def stop_session(agent: str, paths: Paths | None = None) -> bool:
    """Gracefully stop an agent's session: /exit, then kill tmux session.

    Returns True if a session was stopped.
    """
    paths = paths or resolve()
    target = _resolve_session(agent)
    if not session_alive(target):
        return False

    _tmux_run("send-keys", "-t", target, "/exit", "Enter")
    time.sleep(1)
    _tmux_run("kill-session", "-t", target)

    # Update agent status
    if not agent.startswith("@"):
        agent = "@" + agent
    try:
        from .io import atomic_write_text

        agent_dir = paths.agents / agent
        if agent_dir.is_dir():
            atomic_write_text(agent_dir / "status", "idle\n")
    except OSError:
        pass

    try:
        log_event(
            "agent.session",
            f"{agent} session stopped",
            agent=agent,
            paths=paths,
        )
    except Exception:
        pass

    return True


def restart_session(agent: str, reason: str, paths: Paths | None = None) -> bool:
    """Restart claude inside an agent's tmux session.

    Writes a per-agent restart marker and sends /exit. The respawn loop
    brings Claude back; the watchdog injects a wake-up prompt.

    Returns True if the restart was initiated.
    """
    from .gateway.session import restart_agent_session

    target = _resolve_session(agent)
    if not agent.startswith("@"):
        agent = "@" + agent
    return restart_agent_session(agent, reason, target, paths)


def send_to_session(agent: str, message: str, paths: Paths | None = None) -> bool:
    """Send a message to an agent's tmux session.

    Uses the tmux submit helper for reliable delivery.
    Returns True on success.
    """
    paths = paths or resolve()
    target = _resolve_session(agent)
    if not session_alive(target):
        return False

    from .telegram.inject import submit_to_tmux as _submit

    return _submit("@cli", message, session=target)


# ---------------------------------------------------------------------------
# Multi-agent viewer session (``metasphere sessions all``)
# ---------------------------------------------------------------------------

def list_alive_persistent_agents(
    paths: Paths | None = None,
) -> list[tuple[AgentRecord, str]]:
    """Return ``[(agent, session_name), ...]`` for every persistent agent
    whose tmux session is currently alive.

    Walks both global (``~/.metasphere/agents/@*``) and project-scoped
    (``~/.metasphere/projects/*/agents/@*``) directories via
    ``list_agents``.
    """
    paths = paths or resolve()
    out: list[tuple[AgentRecord, str]] = []
    for agent in list_agents(paths):
        if not agent.is_persistent:
            continue
        sname = agent.session_name
        if session_alive(sname):
            out.append((agent, sname))
    return out


def kill_viewer_session(viewer: str = VIEWER_SESSION_NAME) -> bool:
    """Kill the viewer tmux session if it exists. Source sessions are
    unaffected (linked windows are simply dropped).

    Returns True if a session was killed.
    """
    if not session_alive(viewer):
        return False
    _tmux_run("kill-session", "-t", viewer)
    return True


def build_viewer_session(
    viewer: str = VIEWER_SESSION_NAME,
    paths: Paths | None = None,
) -> tuple[str, list[AgentRecord]]:
    """Build (or rebuild) a tmux session showing every alive persistent
    agent as a linked window.

    Idempotent: any pre-existing ``viewer`` session is killed first.
    Returns ``(viewer_name, linked_agents)``. ``linked_agents`` is empty
    if no agents were alive; in that case no viewer session is created.

    Source sessions are not modified — ``link-window`` is non-destructive,
    and tearing down the viewer later via ``kill_viewer_session`` does
    not touch the sources.
    """
    alive = list_alive_persistent_agents(paths)

    # Idempotent rebuild: drop any stale viewer first.
    if session_alive(viewer):
        _tmux_run("kill-session", "-t", viewer)

    if not alive:
        return viewer, []

    # Detached placeholder window at index 0; linked sources get real indices.
    _tmux_run("new-session", "-d", "-s", viewer, "-n", "_placeholder")

    linked: list[AgentRecord] = []
    for idx, (agent, src) in enumerate(alive, start=1):
        r = _tmux_run(
            "link-window",
            "-s", f"{src}:0",
            "-t", f"{viewer}:{idx}",
        )
        if r.returncode == 0:
            linked.append(agent)

    # Drop the placeholder; if nothing linked successfully, tear down the
    # viewer entirely so the caller sees an empty result.
    _tmux_run("kill-window", "-t", f"{viewer}:_placeholder")
    if not linked:
        _tmux_run("kill-session", "-t", viewer)
        return viewer, []

    _tmux_run("select-window", "-t", f"{viewer}:{1}")
    return viewer, linked


def attach_viewer(viewer: str = VIEWER_SESSION_NAME) -> int:
    """Exec ``tmux attach`` to the viewer session. Replaces current proc.

    Returns 1 if the viewer does not exist (does not exec).
    """
    if not session_alive(viewer):
        return 1
    os.execvp(_tmux_bin(), [_tmux_bin(), "attach-session", "-t", viewer])
    return 0  # unreachable
