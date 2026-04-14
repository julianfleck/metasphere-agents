"""Gateway daemon: poll telegram, inject inbound messages, run watchdog.

This is the loop that ties session lifecycle, telegram polling, and the
watchdog together. It is intentionally bulletproof: every loop step is
wrapped in try/except so a single iteration failure cannot exit the
daemon. Every loop step is wrapped in try/except so a single iteration
failure cannot exit the process.
"""

from __future__ import annotations

import time
from typing import Callable, Optional

from ..events import log_event
from ..paths import Paths, resolve
from ..telegram import poller
from .session import ensure_session, write_harness_hash_baseline
from .watchdog import run_watchdog


def _poll_once(timeout: int = 1) -> int:
    """Thin wrapper over ``poller.run_poll_iteration``.

    The poll loop itself (getUpdates → dispatch → save offset) lives in
    ``telegram.poller.run_poll_iteration``. This wrapper exists only to
    log handler errors to the event stream under the ``@gateway`` agent
    — pure bookkeeping. If you need to change polling behavior, change
    it in the poller module.
    """
    def _log_handler_error(u, exc):
        try:
            log_event(
                "telegram.handle_error",
                f"handle_update raised for update {u.update_id}: {exc}",
                agent="@gateway",
            )
        except Exception:
            pass

    return poller.run_poll_iteration(timeout=timeout, on_error=_log_handler_error)


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

    # Refresh harness hash baseline at boot so an existing-on-startup
    # session uses the latest harness as its drift reference.
    try:
        write_harness_hash_baseline(paths)
    except Exception:
        pass

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

    # so the watchdog fires on the first iteration. This is safe because
    # the daemon no longer flap-restarts; the 10s rate-limit marker inside
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
