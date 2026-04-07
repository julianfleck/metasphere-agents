"""metasphere.gateway — persistent orchestrator session + watchdog + daemon.

Python port of ``scripts/metasphere-gateway``. The bash version had four jobs:

1. Maintain a persistent tmux+REPL session for ``@orchestrator``.
2. Poll telegram getUpdates and inject inbound messages into that session.
3. Watchdog: clear stuck-paste placeholders, auto-approve safety-hooks confirmations.
4. Daemon loop tying it all together.

The Python rewrite splits these cleanly across submodules:

- ``session``  — tmux+REPL lifecycle (start/restart/health/ensure).
- ``watchdog`` — stuck-paste recovery + safety-hooks auto-approve.
- ``daemon``   — supervisor loop composing telegram poller + watchdog.

The bash daemon had a known restart-flap bug (``set -e`` tripped inside the
loop body, exiting status=1 every ~6s). The Python rewrite wraps every loop
step in try/except so a single failure cannot exit the daemon.

We deliberately do NOT reimplement tmux paste-submission (invariant 15 — shell
out to ``scripts/metasphere-tmux-submit``) or telegram getUpdates (use
``metasphere.telegram.poller``).
"""

from __future__ import annotations

from .daemon import run_daemon
from .session import (
    SESSION_NAME,
    ensure_session,
    restart_session,
    session_health,
    start_session,
)
from .watchdog import (
    check_safety_hooks_confirmation,
    check_stuck_paste,
    run_watchdog,
)

__all__ = [
    "SESSION_NAME",
    "start_session",
    "restart_session",
    "session_health",
    "ensure_session",
    "check_stuck_paste",
    "check_safety_hooks_confirmation",
    "run_watchdog",
    "run_daemon",
]
