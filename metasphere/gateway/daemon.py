"""Gateway daemon: poll telegram, inject inbound messages, run watchdog.

This is the loop that ties session lifecycle, telegram polling, and the
watchdog together. It is intentionally bulletproof: every loop step is
wrapped in try/except so a single iteration failure cannot exit the
daemon. The bash version had a known restart-flap bug — ``set -e``
tripping inside the loop body caused it to exit status=1 every ~6s
under systemd, which then respawned the script in a tight loop. The
Python rewrite must NOT replicate this.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from ..events import log_event
from ..paths import Paths, resolve
from ..telegram import poller
from ..telegram.commands import Context as _CmdContext, dispatch as _dispatch_command
from ..telegram.api import send_message as _tg_send, set_message_reaction as _tg_react
from ..telegram.inject import submit_to_tmux
from .session import ensure_session
from .watchdog import run_watchdog


def _poll_once(timeout: int = 1) -> int:
    """Single getUpdates call. Inject inbound text into the orchestrator
    session and bump the offset. Returns number of updates processed.
    """
    offset = poller.load_offset()
    updates = poller.get_updates(offset=offset, timeout=timeout)
    for u in updates:
        if u.text and u.chat_id is not None:
            if u.text.startswith("/"):
                # Route slash commands through the command dispatcher (M5,
                # wave-4 review). Previously these were silently dropped.
                # No 👀 reaction here: slash commands bypass the orchestrator
                # loop, replies are immediate, and the reaction is just noise.
                try:
                    ctx = _CmdContext(
                        chat_id=u.chat_id,
                        from_user=u.from_username or "user",
                    )
                    reply = _dispatch_command(u.text, ctx)
                    if reply:
                        try:
                            _tg_send(u.chat_id, reply)
                        except Exception:
                            pass
                except Exception:
                    pass
            else:
                # Orchestrator-routed message: acknowledge receipt with an
                # eye reaction so the user sees the message has been picked
                # up before the agent's response arrives. Regression-fix:
                # the legacy bash poller did this and it got dropped in the
                # python cutover. Best-effort: never let a reaction failure
                # block injection.
                if u.message_id is not None:
                    try:
                        _tg_react(u.chat_id, u.message_id, "👀")
                    except Exception:
                        pass
                submit_to_tmux(f"@{u.from_username or 'user'}", u.text)
        poller.save_offset(u.update_id + 1)
    return len(updates)


def run_daemon(
    paths: Optional[Paths] = None,
    poll_interval: float = 3.0,
    watchdog_interval: float = 5.0,
    *,
    stop: Optional[Callable[[], bool]] = None,
    poll_fn: Optional[Callable[[], int]] = None,
    sleep_fn: Optional[Callable[[float], None]] = None,
    time_fn: Optional[Callable[[], float]] = None,
) -> None:
    """Run the gateway daemon forever.

    The injection points (``poll_fn``, ``sleep_fn``, ``time_fn``,
    ``stop``) exist for tests so a single iteration failure can be
    asserted to NOT exit the daemon. Production callers leave them at
    None and the daemon never returns.
    """
    paths = paths or resolve()
    poll_fn = poll_fn or _poll_once
    sleep_fn = sleep_fn or time.sleep
    time_fn = time_fn or time.time

    try:
        ensure_session(paths)
    except Exception as e:
        try:
            log_event(
                "supervisor.daemon_error",
                f"ensure_session failed at boot: {e}",
                agent="@daemon-supervisor",
                paths=paths,
            )
        except Exception:
            pass

    # Republish slash command manifest to BotFather via setMyCommands.
    # This makes registration automatic on every daemon restart, so any
    # change to BOT_COMMANDS_MANIFEST takes effect by simply restarting
    # the gateway (which already happens after every code deploy).
    # Best-effort: a network blip must NOT block the daemon from booting.
    try:
        from ..telegram.commands import register_bot_commands

        register_bot_commands()
    except Exception as e:
        try:
            log_event(
                "supervisor.daemon_error",
                f"register_bot_commands failed at boot: {e}",
                agent="@daemon-supervisor",
                paths=paths,
            )
        except Exception:
            pass

    # so the watchdog fires on the first iteration. L2 (wave-4 review):
    # this is safe under the rewrite because the daemon no longer
    # flap-restarts; the 10s rate-limit marker inside
    # check_safety_hooks_confirmation is the defence-in-depth.
    last_watchdog = -float("inf")
    while True:
        if stop is not None and stop():
            return

        # 1) Telegram poll. A failure here must NOT exit the daemon.
        try:
            poll_fn()
        except Exception as e:
            try:
                log_event(
                    "supervisor.daemon_error",
                    f"poll_fn raised: {e}",
                    agent="@daemon-supervisor",
                    paths=paths,
                )
            except Exception:
                pass

        # 2) Watchdog tick.
        now = time_fn()
        if now - last_watchdog >= watchdog_interval:
            try:
                run_watchdog(paths)
            except Exception as e:
                try:
                    log_event(
                        "supervisor.daemon_error",
                        f"run_watchdog raised: {e}",
                        agent="@daemon-supervisor",
                        paths=paths,
                    )
                except Exception:
                    pass
            last_watchdog = now

        # 3) Sleep.
        try:
            sleep_fn(poll_interval)
        except Exception:
            return
