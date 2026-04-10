"""Inject incoming text messages into the orchestrator's tmux session.

The orchestrator runs inside a tmux session named
``metasphere-orchestrator``; without direct injection, incoming user
messages would only surface on the next heartbeat tick (up to 5 min
latency).

Uses :mod:`metasphere.tmux` for reliable tmux paste-submission.
"""

from __future__ import annotations

import re

from ..tmux import submit_to_tmux as _tmux_submit

_USERNAME_RE = re.compile(r"[^\w]+")

DEFAULT_SESSION = "metasphere-orchestrator"


def submit_to_tmux(
    from_user: str, text: str, session: str = DEFAULT_SESSION
) -> bool:
    """Submit ``[telegram from <from_user>] <text>`` to the tmux session.

    Returns True on success, False if tmux/script unavailable or session
    missing. Never raises — injection is best-effort.
    """
    # Telegram usernames are attacker-controlled — sanitise to [\w]+ so
    # they can't smuggle slash-command-like prefixes into the orchestrator
    # REPL when the payload is rendered.
    safe_user = _USERNAME_RE.sub("", from_user) or "unknown"
    payload = f"[telegram from {safe_user}] {text}"
    return _tmux_submit(session, payload)
