"""tmux session helpers (port of scripts/metasphere-session).

Lightweight introspection layer over the tmux sessions managed by
``metasphere.agents``. The bash script also handled "start interactive
session" and "send keys" — those overlap heavily with
``agents.spawn_persistent`` and ``gateway`` and are intentionally not
duplicated here.
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from typing import Optional

from .agents import _tmux_bin, session_alive, session_name_for

_SESSION_PREFIX = "metasphere-"


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
