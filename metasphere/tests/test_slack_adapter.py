"""Tests for ``metasphere.gateway.adapters.slack.SlackAdapter``."""

from __future__ import annotations

from unittest.mock import patch

from metasphere.gateway.adapter import SurfaceAdapter
from metasphere.gateway.adapters.slack import SlackAdapter, _derive_target_agent


def test_surface_type_is_slack():
    adapter = SlackAdapter("slack-relay")
    assert adapter.surface_type == "slack"
    assert adapter.surface_id == "slack-relay"


def test_satisfies_surface_adapter_protocol():
    adapter = SlackAdapter("slack-relay")
    assert isinstance(adapter, SurfaceAdapter)


def test_derive_target_agent_strips_prefix():
    assert _derive_target_agent("slack-relay") == "@relay"
    assert _derive_target_agent("slack-cluster-1") == "@cluster-1"
    # Bare legacy single-bot default → orchestrator REPL (no agent body),
    # preserving the pre-route-to-session behaviour of the default surface.
    assert _derive_target_agent("slack") == "@orchestrator"


def test_target_agent_override():
    """Explicit ``target_agent_id`` wins over the derived mapping."""
    adapter = SlackAdapter("slack-relay", target_agent_id="@editorial")
    assert adapter._target_agent_id == "@editorial"


def test_receive_returns_count_from_poller():
    adapter = SlackAdapter("slack-relay")
    with patch(
        "metasphere.gateway.adapters.slack._slack_poller.run_poll_iteration"
    ) as run_poll:
        run_poll.return_value = 3
        n = adapter.receive(timeout=2)
    assert n == 3
    run_poll.assert_called_once_with(
        "slack-relay", "@relay", timeout=2,
    )


def test_send_calls_api_send_message():
    adapter = SlackAdapter("slack-relay")
    with patch(
        "metasphere.gateway.adapters.slack._slack_api.send_message"
    ) as send:
        adapter.send("C12345", "hello")
    send.assert_called_once_with("slack-relay", "C12345", "hello")


def test_send_coerces_int_chat_id_to_string():
    """The Protocol accepts ``int | str``; Slack channel ids are strings."""
    adapter = SlackAdapter("slack-relay")
    with patch(
        "metasphere.gateway.adapters.slack._slack_api.send_message"
    ) as send:
        adapter.send(12345, "hello")
    send.assert_called_once_with("slack-relay", "12345", "hello")
