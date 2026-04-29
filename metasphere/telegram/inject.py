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
    from_user: str,
    text: str,
    session: str = DEFAULT_SESSION,
    *,
    defer_if_busy: bool = False,
    escape_prefix: bool = True,
) -> bool:
    """Submit ``[telegram from <from_user>] <text>`` to the tmux session.

    Returns True on success, False if tmux/script unavailable or session
    missing. Never raises — injection is best-effort.

    *defer_if_busy* is forwarded to :func:`metasphere.tmux.submit_to_tmux`;
    user-inbound telegram leaves it False (always fire), restart-wake
    passes True (defer on human typing). *escape_prefix* defaults True
    for user-inbound telegram (clobber any running tool — "only
    user-inbound interrupts", operator 2026-04-16); restart-wake passes
    False so it doesn't cut a mid-tool-call on the newly-respawned pane.
    """
    # Telegram usernames are attacker-controlled — sanitise to [\w]+ so
    # they can't smuggle slash-command-like prefixes into the orchestrator
    # REPL when the payload is rendered.
    safe_user = _USERNAME_RE.sub("", from_user) or "unknown"
    payload = f"[telegram from {safe_user}] {text}"
    ok = _tmux_submit(
        session,
        payload,
        defer_if_busy=defer_if_busy,
        escape_prefix=escape_prefix,
    )
    if ok and session == DEFAULT_SESSION:
        # Telegram inject is the canonical "user just spoke to the
        # orchestrator" signal — refresh last_active so reap_dormant
        # treats this session as active even when no terminal output
        # follows immediately (model thinking, deferred-busy paste).
        try:
            from ..agents import touch_last_active
            touch_last_active("@orchestrator")
        except Exception:
            pass
    return ok
