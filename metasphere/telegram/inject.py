"""Inject incoming text messages into the orchestrator's tmux session.

The orchestrator runs inside a tmux session named
``metasphere-orchestrator``; without direct injection, incoming user
messages would only surface on the next heartbeat tick (up to 5 min
latency).

Shells out to ``scripts/metasphere-tmux-submit`` for the actual tmux
paste-submission.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess

_USERNAME_RE = re.compile(r"[^\w]+")

DEFAULT_SESSION = "metasphere-orchestrator"
SUBMIT_SCRIPT = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "scripts",
    "metasphere-tmux-submit",
)


def submit_to_tmux(
    from_user: str, text: str, session: str = DEFAULT_SESSION
) -> bool:
    """Submit ``[telegram from <from_user>] <text>`` to the tmux session.

    Returns True on success, False if tmux/script unavailable or session
    missing. Never raises — injection is best-effort.
    """
    tmux = shutil.which("tmux")
    if not tmux:
        return False
    has = subprocess.run(
        [tmux, "has-session", "-t", session],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if has.returncode != 0:
        return False
    if not os.path.exists(SUBMIT_SCRIPT):
        return False
    # Telegram usernames are attacker-controlled — sanitise to [\w]+ so
    # they can't smuggle slash-command-like prefixes into the orchestrator
    # REPL when the payload is rendered.
    safe_user = _USERNAME_RE.sub("", from_user) or "unknown"
    payload = f"[telegram from {safe_user}] {text}"
    # The script is sourced for its submit_to_tmux function. Invoke a
    # small bash one-liner that sources it and calls the function.
    cmd = [
        "bash",
        "-c",
        'source "$1"; TMUX_CMD="$2" submit_to_tmux "$3" "$4"',
        "_",
        SUBMIT_SCRIPT,
        tmux,
        session,
        payload,
    ]
    proc = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return proc.returncode == 0
