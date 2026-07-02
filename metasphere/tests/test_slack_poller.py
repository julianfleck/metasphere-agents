"""Tests for ``metasphere.slack.poller`` — slash command wiring + drain.

The background SocketModeHandler worker is bypassed (no websocket / network);
the bolt command handler is exercised via the extracted
``_build_command_handler`` factory.
"""

from __future__ import annotations

import queue as _queue
from unittest.mock import MagicMock

import pytest

from metasphere.slack import handler as _handler
from metasphere.slack import poller as _poller
from metasphere.slack import routing as _routing


@pytest.fixture(autouse=True)
def _reset_poller():
    _poller._reset_for_tests()
    yield
    _poller._reset_for_tests()


def _seed(monkeypatch, surface_id, events):
    q: "_queue.Queue" = _queue.Queue()
    for item in events:
        q.put(item)

    def _fake_start(sid, target, *, app_factory=None):  # noqa: ARG001
        _poller._QUEUES[sid] = q

    monkeypatch.setattr(_poller, "_start_socket_mode_worker", _fake_start)


# --------------------------------------------------------------------------
# drain routing
# --------------------------------------------------------------------------

def test_drain_routes_command_to_handle_command(monkeypatch):
    _seed(monkeypatch, "slack", [
        ("message", {"channel": "D1"}),
        ("command", {"command": "/demo", "text": "x", "channel_id": "C1"}),
    ])
    calls: list = []
    monkeypatch.setattr(_handler, "handle_dm", lambda e, **k: calls.append(("dm", k)))
    monkeypatch.setattr(_handler, "handle_command",
                        lambda e, **k: calls.append(("command", k)))

    n = _poller.run_poll_iteration("slack", "@orchestrator")

    assert n == 2
    assert [c[0] for c in calls] == ["dm", "command"]
    # The command branch is resolver-driven (handle_command defaults to
    # slash_resolver); the drain does not pass a bare target_agent_id.
    command_kwargs = calls[1][1]
    assert command_kwargs["surface_id"] == "slack"
    assert "target_agent_id" not in command_kwargs


def test_drain_isolates_command_handler_exceptions(monkeypatch):
    _seed(monkeypatch, "slack", [
        ("command", {"command": "/demo"}),
        ("command", {"command": "/demo"}),
    ])
    monkeypatch.setattr(
        _handler, "handle_command",
        MagicMock(side_effect=RuntimeError("boom")),
    )
    n = _poller.run_poll_iteration("slack", "@orchestrator")
    assert n == 2  # both drained despite raising


# --------------------------------------------------------------------------
# bolt command handler: ack-then-enqueue ordering (3s budget)
# --------------------------------------------------------------------------

def test_command_handler_acks_then_enqueues_on_match(monkeypatch):
    monkeypatch.setattr(
        _routing, "slash_resolver",
        lambda payload, surface_id: ("@demo-agent", "status?"),
    )
    order: list = []
    enqueued: list = []
    ack = MagicMock(side_effect=lambda **kw: order.append("ack"))

    def enqueue(item):
        order.append("enqueue")
        enqueued.append(item)

    handler = _poller._build_command_handler("slack", enqueue)
    payload = {"command": "/demo", "text": "status?", "channel_id": "C1"}
    handler(ack, payload)

    # ACK happens before the (slow) enqueue → drain work.
    assert order == ["ack", "enqueue"]
    # in_channel → Slack surfaces the user's own command; the app's text is a
    # routing CONFIRMATION (names the target), NOT an echo of the user message.
    assert ack.call_args.kwargs["response_type"] == "in_channel"
    assert ack.call_args.kwargs["text"] == "routing to @demo-agent…"
    assert enqueued == [("command", payload)]


def test_command_handler_acks_help_and_skips_enqueue_on_no_agent(monkeypatch):
    monkeypatch.setattr(
        _routing, "slash_resolver",
        lambda payload, surface_id: (None, "no agent mapped for /nope"),
    )
    enqueued: list = []
    ack = MagicMock()

    handler = _poller._build_command_handler("slack", lambda item: enqueued.append(item))
    handler(ack, {"command": "/nope", "text": "", "channel_id": "C1"})

    ack.assert_called_once()
    assert "no agent mapped" in ack.call_args.kwargs["text"]
    assert enqueued == []  # nothing to route


def test_command_handler_acks_even_if_resolver_raises(monkeypatch):
    monkeypatch.setattr(
        _routing, "slash_resolver",
        MagicMock(side_effect=RuntimeError("resolver blew up")),
    )
    enqueued: list = []
    ack = MagicMock()
    handler = _poller._build_command_handler("slack", lambda item: enqueued.append(item))
    handler(ack, {"command": "/demo"})
    ack.assert_called_once()  # never miss the 3s ack
    assert enqueued == []
