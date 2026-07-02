"""Slack Socket Mode driver — surface-id-keyed background lifecycle.

Slack Socket Mode uses a long-lived websocket, not a poll. The
gateway daemon's poll loop calls :func:`run_poll_iteration` per tick
expecting a count of events handled; for Slack that count is "events
processed off the in-process queue since the last tick".

To make that mesh with the daemon's sync poll-tick model, this
module starts a SocketModeHandler in a background thread (one per
surface_id), pushes incoming events into a queue, and the daemon
tick drains the queue per call.
"""

from __future__ import annotations

import logging
import queue
import threading
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# One queue + worker per surface_id. Idempotent start: re-calling
# ``run_poll_iteration`` reuses the existing background thread rather
# than spinning up duplicates.
_QUEUES: dict[str, "queue.Queue[Any]"] = {}
_WORKERS: dict[str, threading.Thread] = {}
_LOCK = threading.Lock()


def _build_command_handler(surface_id: str, enqueue: Callable[[Any], None]):
    """Build the bolt ``command`` listener for ``surface_id``.

    Returned closure resolves + acks SYNCHRONOUSLY (the 3s budget) on the bolt
    thread, then enqueues ``("command", payload)`` for the drain tick when an
    agent was selected. Extracted from ``_build_app`` so the ack-then-enqueue
    ordering is unit-testable without a websocket. ``enqueue`` takes a single
    ``(kind, payload)`` tuple (matches ``queue.Queue.put``).
    """
    from . import routing as _routing

    def _on_command(ack, command):
        payload = dict(command)
        try:
            target, message = _routing.slash_resolver(payload, surface_id)
        except Exception as e:  # noqa: BLE001 — never miss the ack
            logger.warning("slack command resolve failed: %r", e)
            target, message = None, "internal error routing that command"
        if target:
            # ``in_channel`` makes Slack render the user's OWN slash command
            # (attributed to them) — so they SEE their message as theirs. The
            # app's text is then a routing CONFIRMATION posted after it (not an
            # echo of their words). Ephemeral would hide their command; a bare
            # ack swallows it entirely. The agent's reply follows, carrying its
            # own ``[BOT <agent>]: `` attribution.
            ack(response_type="in_channel", text=f"routing to {target}…")
            enqueue(("command", payload))
        else:
            # No agent selected — show the help/error, nothing to drain.
            ack(response_type="ephemeral", text=message or "no agent mapped")

    return _on_command


def _start_socket_mode_worker(
    surface_id: str,
    target_agent_id: str,
    *,
    app_factory: Optional[Callable[..., Any]] = None,
) -> None:
    """Spin up the background SocketModeHandler thread for ``surface_id``.

    The worker pushes inbound events into ``_QUEUES[surface_id]``; the
    poll-tick drains them. ``app_factory`` lets tests inject a stub
    instead of constructing a real slack-bolt App.
    """
    with _LOCK:
        if surface_id in _WORKERS and _WORKERS[surface_id].is_alive():
            return
        evt_queue: "queue.Queue[Any]" = queue.Queue()
        _QUEUES[surface_id] = evt_queue

        def _build_app():
            if app_factory is not None:
                return app_factory(surface_id=surface_id,
                                   target_agent_id=target_agent_id,
                                   event_queue=evt_queue)
            from slack_bolt import App
            from slack_bolt.adapter.socket_mode import SocketModeHandler

            import re

            from . import api as _api

            bot_token, app_token = _api._load_tokens(surface_id)
            app = App(token=bot_token)

            # Wire handlers that enqueue + ack — the slack-bolt thread
            # owns the websocket; the queue + tick decouples it from
            # the gateway daemon's main loop.
            def _enqueue_message(event, ack=None):
                if ack:
                    ack()
                evt_queue.put(("message", event))

            def _enqueue_mention(event, ack=None):
                if ack:
                    ack()
                evt_queue.put(("app_mention", event))

            app.event("message")(_enqueue_message)
            app.event("app_mention")(_enqueue_mention)

            # Slash commands: ONE catch-all handler (Slack only delivers
            # commands the operator registered in app-config, so the regex only
            # ever sees those). The 3s ack budget is met SYNCHRONOUSLY on the
            # bolt thread — resolve the target (cheap config + roster lookup),
            # ack an ephemeral status, THEN enqueue the slow route-to-session
            # work for the drain tick.
            app.command(re.compile(r"/.*"))(
                _build_command_handler(surface_id, evt_queue.put)
            )

            socket_handler = SocketModeHandler(app, app_token)
            return socket_handler

        def _run():
            try:
                socket_handler = _build_app()
                # SocketModeHandler.start() blocks on the websocket.
                socket_handler.start()
            except Exception as e:  # noqa: BLE001
                logger.error(
                    "slack socket-mode worker for %s crashed: %r",
                    surface_id, e,
                )

        t = threading.Thread(target=_run, daemon=True,
                             name=f"slack-{surface_id}")
        t.start()
        _WORKERS[surface_id] = t


def run_poll_iteration(
    surface_id: str,
    target_agent_id: str,
    timeout: int = 1,
    *,
    app_factory: Optional[Callable[..., Any]] = None,
    on_event: Optional[Callable[[str, dict], None]] = None,
) -> int:
    """Drain queued events for ``surface_id``.

    Starts the background SocketModeHandler thread on first call (one
    per surface_id; idempotent on subsequent calls). Returns the count
    of events drained this tick — matches the
    :class:`metasphere.gateway.adapter.SurfaceAdapter` Protocol's
    ``receive`` shape.

    ``app_factory`` and ``on_event`` are test seams: pass an
    ``app_factory`` to skip the real slack-bolt App; pass ``on_event``
    to capture events instead of routing them through the real handler.
    """
    _start_socket_mode_worker(
        surface_id, target_agent_id, app_factory=app_factory,
    )
    evt_queue = _QUEUES.get(surface_id)
    if evt_queue is None:
        return 0

    n = 0
    while True:
        try:
            kind, event = evt_queue.get_nowait()
        except queue.Empty:
            break
        n += 1
        if on_event is not None:
            try:
                on_event(kind, event)
            except Exception:  # noqa: BLE001
                logger.exception("slack on_event callback raised")
            continue
        try:
            from . import handler as _handler
            if kind == "message":
                _handler.handle_dm(
                    event, surface_id=surface_id,
                    target_agent_id=target_agent_id,
                )
            elif kind == "app_mention":
                _handler.handle_mention(
                    event, surface_id=surface_id,
                    target_agent_id=target_agent_id,
                )
            elif kind == "command":
                # Slash command → route-to-session core (resolver-driven).
                _handler.handle_command(event, surface_id=surface_id)
        except Exception:  # noqa: BLE001
            logger.exception("slack handler raised for kind=%s", kind)
    return n


def _reset_for_tests() -> None:
    """Clear all queues + workers. Used by tests to isolate fixtures."""
    with _LOCK:
        _QUEUES.clear()
        _WORKERS.clear()
