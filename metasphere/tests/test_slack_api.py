"""Tests for ``metasphere.slack.api`` — token loading + send paths.

Network calls are mocked at the WebClient boundary so no real Slack
workspace is needed.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest

from metasphere.slack import api as _api


@pytest.fixture(autouse=True)
def _isolate_env(monkeypatch, tmp_path):
    """Point CONFIG_DIR at a tmp_path so env-file reads don't leak into
    the operator's ~/.metasphere/config/."""
    monkeypatch.delenv("SLACK_BOT_TOKEN", raising=False)
    monkeypatch.delenv("SLACK_APP_TOKEN", raising=False)
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    monkeypatch.setattr(_api, "CONFIG_DIR", str(cfg_dir))
    return cfg_dir


def _write_env(cfg_dir, filename: str, body: str) -> None:
    (cfg_dir / filename).write_text(body)


def test_load_tokens_from_env_file(_isolate_env):
    _write_env(
        _isolate_env, "slack-relay.env",
        "SLACK_BOT_TOKEN=xoxb-test\nSLACK_APP_TOKEN=xapp-test\n",
    )
    bot, app = _api._load_tokens("slack-relay")
    assert bot == "xoxb-test"
    assert app == "xapp-test"


def test_load_tokens_falls_back_to_default(_isolate_env):
    """No per-surface file → fall back to slack.env."""
    _write_env(
        _isolate_env, "slack.env",
        "SLACK_BOT_TOKEN=xoxb-default\nSLACK_APP_TOKEN=xapp-default\n",
    )
    bot, app = _api._load_tokens("slack-unknown")
    assert bot == "xoxb-default"
    assert app == "xapp-default"


def test_load_tokens_missing_raises_filenotfound(_isolate_env):
    with pytest.raises(FileNotFoundError) as exc:
        _api._load_tokens("slack-missing")
    msg = str(exc.value)
    # Operator-facing message names the file paths to populate.
    assert "slack-missing.env" in msg
    assert "SLACK_BOT_TOKEN" in msg


def test_load_tokens_env_vars_win(_isolate_env, monkeypatch):
    monkeypatch.setenv("SLACK_BOT_TOKEN", "xoxb-env")
    monkeypatch.setenv("SLACK_APP_TOKEN", "xapp-env")
    _write_env(
        _isolate_env, "slack-relay.env",
        "SLACK_BOT_TOKEN=xoxb-from-file\n",
    )
    bot, app = _api._load_tokens("slack-relay")
    assert bot == "xoxb-env"
    assert app == "xapp-env"


def test_send_message_calls_chat_postmessage(_isolate_env):
    """send_message routes through WebClient.chat_postMessage with the
    expected args."""
    _write_env(_isolate_env, "slack-relay.env",
               "SLACK_BOT_TOKEN=xoxb-test\n")
    fake_client = MagicMock()
    fake_client.chat_postMessage.return_value = MagicMock(
        data={"ok": True, "ts": "123.456"},
    )
    with patch.object(_api, "_web_client", lambda token: fake_client):
        responses = _api.send_message("slack-relay", "C12345", "hello")
    fake_client.chat_postMessage.assert_called_once_with(
        channel="C12345", text="hello",
    )
    assert responses == [{"ok": True, "ts": "123.456"}]


def test_send_message_chunks_long_text(_isolate_env):
    """A message past CHUNK_MAX → multiple chat_postMessage calls with
    a [i/N] marker prefix."""
    _write_env(_isolate_env, "slack-relay.env",
               "SLACK_BOT_TOKEN=xoxb-test\n")
    # 2.5x the chunk size so we expect 3 chunks.
    long = ("paragraph.\n\n" * 1000)[: _api.CHUNK_MAX * 2 + 100]
    fake_client = MagicMock()
    fake_client.chat_postMessage.return_value = MagicMock(
        data={"ok": True},
    )
    with patch.object(_api, "_web_client", lambda token: fake_client):
        _api.send_message("slack-relay", "C12345", long)
    assert fake_client.chat_postMessage.call_count >= 2
    first_call_text = fake_client.chat_postMessage.call_args_list[0].kwargs["text"]
    assert first_call_text.startswith("[1/")


def test_send_message_empty_text_rejected(_isolate_env):
    _write_env(_isolate_env, "slack-relay.env",
               "SLACK_BOT_TOKEN=xoxb-test\n")
    with pytest.raises(ValueError):
        _api.send_message("slack-relay", "C12345", "")


def test_send_document_calls_files_upload_v2(_isolate_env, tmp_path):
    _write_env(_isolate_env, "slack-relay.env",
               "SLACK_BOT_TOKEN=xoxb-test\n")
    fpath = tmp_path / "report.txt"
    fpath.write_text("report content")
    fake_client = MagicMock()
    fake_client.files_upload_v2.return_value = MagicMock(
        data={"ok": True, "file": {"id": "F1"}},
    )
    with patch.object(_api, "_web_client", lambda token: fake_client):
        resp = _api.send_document(
            "slack-relay", "C12345", str(fpath), title="Report",
        )
    fake_client.files_upload_v2.assert_called_once_with(
        channel="C12345", file=str(fpath), title="Report",
    )
    assert resp == {"ok": True, "file": {"id": "F1"}}


def test_send_with_cc_no_longer_mirrors_to_orchestrator(_isolate_env, monkeypatch):
    """The outbound-CC mirror was removed: sending must NOT touch the message
    bus (no @orchestrator inbox copy)."""
    _write_env(_isolate_env, "slack-relay.env", "SLACK_BOT_TOKEN=xoxb-test\n")
    fake_client = MagicMock()
    fake_client.chat_postMessage.return_value = MagicMock(data={"ok": True})
    import metasphere.messages as _messages
    bus_calls: list = []
    monkeypatch.setattr(_messages, "send_message",
                        lambda *a, **k: bus_calls.append((a, k)))
    with patch.object(_api, "_web_client", lambda token: fake_client):
        _api.send_with_cc(
            "slack-relay", "C12345", "hello", sender_agent_id="@relay",
        )
    assert bus_calls == []  # mirror gone — nothing copied to the bus
    assert not hasattr(_api, "_cc_outbound_to_orchestrator")


def test_send_with_cc_prepends_bot_name(_isolate_env):
    """The posted text is attributed ``[BOT <agent>]: …`` so it's clear which
    agent is speaking through the shared Slack app identity."""
    _write_env(_isolate_env, "slack-relay.env", "SLACK_BOT_TOKEN=xoxb-test\n")
    fake_client = MagicMock()
    fake_client.chat_postMessage.return_value = MagicMock(data={"ok": True})
    with patch.object(_api, "_web_client", lambda token: fake_client):
        _api.send_with_cc(
            "slack-relay", "C1", "die Änderungen sind zusammengefasst",
            sender_agent_id="@relay",
        )
    sent = fake_client.chat_postMessage.call_args.kwargs["text"]
    assert sent == "[BOT relay]: die Änderungen sind zusammengefasst"


def test_send_with_cc_does_not_double_prefix(_isolate_env):
    """Text already carrying a [BOT …] marker is not re-prefixed."""
    _write_env(_isolate_env, "slack-relay.env", "SLACK_BOT_TOKEN=xoxb-test\n")
    fake_client = MagicMock()
    fake_client.chat_postMessage.return_value = MagicMock(data={"ok": True})
    with patch.object(_api, "_web_client", lambda token: fake_client):
        _api.send_with_cc(
            "slack-relay", "C1", "[BOT relay]: already tagged",
            sender_agent_id="@relay",
        )
    sent = fake_client.chat_postMessage.call_args.kwargs["text"]
    assert sent == "[BOT relay]: already tagged"


# ---------- user-name resolution (mocked WebClient; never live) ----------

def _resp(data):
    """Wrap a dict so it exposes ``.data`` like a real SlackResponse."""
    m = MagicMock()
    m.data = data
    return m


def test_resolve_user_name_via_mocked_client():
    client = MagicMock()
    client.users_info.return_value = _resp(
        {"ok": True, "user": {"id": "U1", "profile": {"display_name": "bob"}}}
    )
    assert _api.resolve_user_name("slack", "U1", client=client) == "bob"
    client.users_info.assert_called_once_with(user="U1")


def test_resolve_user_name_falls_back_through_name_fields():
    client = MagicMock()
    client.users_info.return_value = _resp(
        {"user": {"id": "U2", "profile": {}, "real_name": "Real Name"}}
    )
    assert _api.resolve_user_name("slack", "U2", client=client) == "Real Name"


def test_resolve_user_name_missing_scope_returns_none():
    """A SlackApiError (e.g. missing_scope) degrades to None, not a crash."""
    client = MagicMock()
    client.users_info.side_effect = Exception("missing_scope")
    assert _api.resolve_user_name("slack", "U3", client=client) is None


def test_list_users_paginates_and_filters():
    client = MagicMock()
    page1 = _resp({
        "members": [
            {"id": "U1", "profile": {"display_name": "bob"}},
            {"id": "B1", "is_bot": True, "name": "botty"},
            {"id": "USLACKBOT", "name": "slackbot"},
            {"id": "U2", "deleted": True, "name": "ghost"},
        ],
        "response_metadata": {"next_cursor": "CURSOR2"},
    })
    page2 = _resp({
        "members": [{"id": "U3", "real_name": "Alice"}],
        "response_metadata": {"next_cursor": ""},
    })
    client.users_list.side_effect = [page1, page2]
    out = _api.list_users("slack", client=client)
    assert out == [{"id": "U1", "name": "bob"}, {"id": "U3", "name": "Alice"}]
    assert client.users_list.call_count == 2


def test_list_users_missing_scope_returns_empty():
    client = MagicMock()
    client.users_list.side_effect = Exception("missing_scope")
    assert _api.list_users("slack", client=client) == []
