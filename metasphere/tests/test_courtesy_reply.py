"""Tests for the gateway rate-limit courtesy reply.

Covers the surface-agnostic core in :mod:`metasphere.cli.failsafe`
(``parse_reset_time``, ``courtesy_message``, ``_should_courtesy``,
``maybe_courtesy_reply``) plus the inbound wiring in the Telegram and
Slack handlers — i.e. when an agent's pane shows a usage/rate limit, the
originating user gets ONE courtesy reply on the right surface+channel.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from metasphere.cli import failsafe


@pytest.fixture(autouse=True)
def _reset_courtesy_state():
    """Each test starts with an empty per-agent dedup map."""
    failsafe._last_courtesy.clear()
    yield
    failsafe._last_courtesy.clear()


_RATE_LIMITED_PANE = (
    "● Thinking…\n"
    "Claude usage limit reached. Your limit will reset at 3:00 PM (UTC).\n"
)


# ---------------------------------------------------------------------------
# parse_reset_time
# ---------------------------------------------------------------------------


class TestParseResetTime:
    def test_resets_bare_time(self):
        assert failsafe.parse_reset_time("5-hour limit reached · Resets 3pm") == "3pm"

    def test_reset_at_with_seconds_and_tz(self):
        out = failsafe.parse_reset_time(
            "usage limit reached — your limit will reset at 3:00 PM (UTC)"
        )
        assert out == "3:00 PM (UTC)"

    def test_reset_24h_clock(self):
        assert failsafe.parse_reset_time("resets at 14:00") == "14:00"

    def test_no_reset_returns_none(self):
        assert failsafe.parse_reset_time("rate_limit_error: slow down") is None

    def test_empty_returns_none(self):
        assert failsafe.parse_reset_time("") is None

    def test_only_inspects_tail(self):
        # Reset string scrolled above the tail window must not be picked up.
        preamble = "noise\n" * 200
        assert failsafe.parse_reset_time("Resets 9am\n" + preamble) is None


# ---------------------------------------------------------------------------
# courtesy_message
# ---------------------------------------------------------------------------


class TestCourtesyMessage:
    def test_includes_reset_when_known(self):
        msg = failsafe.courtesy_message("3pm")
        assert "usage limit" in msg
        assert "resets 3pm" in msg

    def test_generic_when_unknown(self):
        msg = failsafe.courtesy_message(None)
        assert "usage limit" in msg
        assert "resets" not in msg


# ---------------------------------------------------------------------------
# _should_courtesy (dedup gate)
# ---------------------------------------------------------------------------


class TestShouldCourtesy:
    def test_first_hit_fires(self):
        assert failsafe._should_courtesy("@a", "3pm") is True

    def test_repeat_within_cooldown_suppressed(self):
        failsafe._mark_courtesy("@a", "3pm")
        assert failsafe._should_courtesy("@a", "3pm") is False

    def test_new_reset_window_bypasses_cooldown(self):
        failsafe._mark_courtesy("@a", "3pm")
        # A different reset time means a fresh limit window → notify again.
        assert failsafe._should_courtesy("@a", "5pm") is True

    def test_per_agent_independent(self):
        failsafe._mark_courtesy("@a", "3pm")
        assert failsafe._should_courtesy("@b", "3pm") is True

    def test_cooldown_elapsed_refires(self, monkeypatch):
        failsafe._mark_courtesy("@a", None)
        later = failsafe.time.monotonic() + failsafe._COURTESY_COOLDOWN_SECONDS + 1
        monkeypatch.setattr(failsafe.time, "monotonic", lambda: later)
        assert failsafe._should_courtesy("@a", None) is True


# ---------------------------------------------------------------------------
# maybe_courtesy_reply
# ---------------------------------------------------------------------------


class TestMaybeCourtesyReply:
    def test_sends_on_rate_limit(self, monkeypatch):
        monkeypatch.setattr(failsafe, "_capture_pane", lambda s: _RATE_LIMITED_PANE)
        send = MagicMock()
        assert failsafe.maybe_courtesy_reply("@a", "sess", send) is True
        send.assert_called_once()
        assert "resets 3:00 PM (UTC)" in send.call_args.args[0]

    def test_no_send_when_pane_clean(self, monkeypatch):
        monkeypatch.setattr(failsafe, "_capture_pane", lambda s: "all good [idle]")
        send = MagicMock()
        assert failsafe.maybe_courtesy_reply("@a", "sess", send) is False
        send.assert_not_called()

    def test_no_send_when_capture_empty(self, monkeypatch):
        monkeypatch.setattr(failsafe, "_capture_pane", lambda s: "")
        send = MagicMock()
        assert failsafe.maybe_courtesy_reply("@a", "sess", send) is False
        send.assert_not_called()

    def test_dedup_only_one_per_window(self, monkeypatch):
        monkeypatch.setattr(failsafe, "_capture_pane", lambda s: _RATE_LIMITED_PANE)
        send = MagicMock()
        assert failsafe.maybe_courtesy_reply("@a", "sess", send) is True
        assert failsafe.maybe_courtesy_reply("@a", "sess", send) is False
        send.assert_called_once()

    def test_send_failure_swallowed(self, monkeypatch):
        monkeypatch.setattr(failsafe, "_capture_pane", lambda s: _RATE_LIMITED_PANE)
        send = MagicMock(side_effect=RuntimeError("network down"))
        # Must never raise into the inbound path.
        assert failsafe.maybe_courtesy_reply("@a", "sess", send) is False


# ---------------------------------------------------------------------------
# Telegram inbound wiring
# ---------------------------------------------------------------------------


def _tg_update(text="status?", chat_id=42, thread_id=None):
    from metasphere.telegram import poller

    return poller.Update(
        update_id=1,
        message_id=10,
        chat_id=chat_id,
        chat_is_forum=False,
        thread_id=thread_id,
        from_username="alice",
        text=text,
        date=1700000000,
        chat_type="private",
        raw={"message": {"message_id": 10, "text": text,
                         "chat": {"id": chat_id, "type": "private"}}},
    )


def _tg_hermetic(monkeypatch, handler):
    """Stub the Telegram handler's side-effecting deps (archive, debug log,
    session start, active-conversation pin, bot identity) so a handle_update
    test never touches tmux or the real ``~/.metasphere``."""
    monkeypatch.setattr(handler.api, "bot_identity",
                        lambda: {"username": "bot", "id": 1})
    monkeypatch.setattr(handler.archiver, "archive_message", lambda *a, **k: None)
    monkeypatch.setattr(handler.archiver, "save_latest", lambda *a, **k: None)
    monkeypatch.setattr(handler.attachments, "debug_log", lambda *a, **k: None)
    monkeypatch.setattr("metasphere.gateway.session.start_session",
                        lambda *a, **k: True)
    monkeypatch.setattr("metasphere.routing.active.set_active_conversation",
                        lambda *a, **k: None)


def _slack_hermetic(monkeypatch, slack_api):
    """Stub the Slack handler's side-effecting deps so a handle_dm test never
    touches the real contacts store / active-conversation pin."""
    monkeypatch.setattr(slack_api, "resolve_user_name", lambda *a, **k: None)
    monkeypatch.setattr("metasphere.telegram.archiver.archive_message",
                        lambda *a, **k: None)
    monkeypatch.setattr("metasphere.contacts.reverse_lookup",
                        lambda *a, **k: None)
    monkeypatch.setattr("metasphere.routing.active.set_active_conversation",
                        lambda *a, **k: None)


class TestTelegramWiring:
    def test_dm_on_rate_limit_sends_courtesy_to_chat(self, monkeypatch):
        from metasphere.telegram import handler
        from metasphere import session as _session

        monkeypatch.setattr(failsafe, "_capture_pane", lambda s: _RATE_LIMITED_PANE)
        monkeypatch.setattr(_session, "_resolve_session",
                            lambda a: "metasphere-orchestrator")
        _tg_hermetic(monkeypatch, handler)

        sender = MagicMock()
        tmux_submit = MagicMock(return_value=True)
        handler.handle_update(
            _tg_update(),
            sender=sender,
            reactor=MagicMock(),
            tmux_submit=tmux_submit,
            save_chat_id=MagicMock(),
            write_pending_ack=MagicMock(),
        )

        # Courtesy reply landed on the originating chat …
        sender.assert_called_once()
        assert sender.call_args.args[0] == 42
        assert "usage limit" in sender.call_args.args[1]
        # … and the inbound was STILL injected (processed when limit clears).
        tmux_submit.assert_called_once()

    def test_dm_clean_pane_no_courtesy(self, monkeypatch):
        from metasphere.telegram import handler
        from metasphere import session as _session

        monkeypatch.setattr(failsafe, "_capture_pane", lambda s: "ready [idle]")
        monkeypatch.setattr(_session, "_resolve_session",
                            lambda a: "metasphere-orchestrator")
        _tg_hermetic(monkeypatch, handler)

        sender = MagicMock()
        tmux_submit = MagicMock(return_value=True)
        handler.handle_update(
            _tg_update(),
            sender=sender,
            reactor=MagicMock(),
            tmux_submit=tmux_submit,
            save_chat_id=MagicMock(),
            write_pending_ack=MagicMock(),
        )
        sender.assert_not_called()
        tmux_submit.assert_called_once()


# ---------------------------------------------------------------------------
# Slack inbound wiring
# ---------------------------------------------------------------------------


class TestSlackWiring:
    def test_dm_on_rate_limit_sends_courtesy_to_channel(self, monkeypatch):
        from metasphere.slack import handler
        from metasphere.slack import api as slack_api
        from metasphere import agents as _agents
        from metasphere import session as _session

        monkeypatch.setattr(failsafe, "_capture_pane", lambda s: _RATE_LIMITED_PANE)
        monkeypatch.setattr(_session, "_resolve_session",
                            lambda a: f"metasphere-{a.lstrip('@')}")
        monkeypatch.setattr(_agents, "session_alive", lambda name: True)
        monkeypatch.setattr(_agents, "touch_last_active", MagicMock())
        _slack_hermetic(monkeypatch, slack_api)
        slack_send = MagicMock()
        monkeypatch.setattr(slack_api, "send_message", slack_send)

        tmux_submit = MagicMock(return_value=True)
        ok = handler.handle_dm(
            {"type": "message", "channel_type": "im", "channel": "D9",
             "text": "hi", "user": "U1", "ts": "1700000000.0001"},
            surface_id="slack-relay",
            target_agent_id="@relay",
            tmux_submit=tmux_submit,
        )
        assert ok is True
        # Courtesy reply posted to the SAME channel via slack send …
        slack_send.assert_called_once()
        assert slack_send.call_args.args[0] == "slack-relay"
        assert slack_send.call_args.args[1] == "D9"
        assert "usage limit" in slack_send.call_args.args[2]
        # … and the inbound still delivered into the agent's session.
        tmux_submit.assert_called_once()

    def test_dm_clean_pane_no_courtesy(self, monkeypatch):
        from metasphere.slack import handler
        from metasphere.slack import api as slack_api
        from metasphere import agents as _agents
        from metasphere import session as _session

        monkeypatch.setattr(failsafe, "_capture_pane", lambda s: "ready [idle]")
        monkeypatch.setattr(_session, "_resolve_session",
                            lambda a: f"metasphere-{a.lstrip('@')}")
        monkeypatch.setattr(_agents, "session_alive", lambda name: True)
        monkeypatch.setattr(_agents, "touch_last_active", MagicMock())
        _slack_hermetic(monkeypatch, slack_api)
        slack_send = MagicMock()
        monkeypatch.setattr(slack_api, "send_message", slack_send)

        ok = handler.handle_dm(
            {"type": "message", "channel_type": "im", "channel": "D9",
             "text": "hi", "user": "U1", "ts": "1700000000.0001"},
            surface_id="slack-relay",
            target_agent_id="@relay",
            tmux_submit=MagicMock(return_value=True),
        )
        assert ok is True
        slack_send.assert_not_called()
