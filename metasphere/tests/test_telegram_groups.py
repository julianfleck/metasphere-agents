from unittest.mock import patch

import pytest

from metasphere.telegram import groups as g


def _setup_forum(tmp_paths, forum_id="-1001234567890"):
    (tmp_paths.config).mkdir(parents=True, exist_ok=True)
    (tmp_paths.config / "telegram_forum_id").write_text(forum_id)


def test_create_topic_persists(tmp_paths):
    _setup_forum(tmp_paths)
    fake_resp = {"ok": True, "result": {"message_thread_id": 42, "name": "Alpha"}}
    with patch("metasphere.telegram.groups.tg_api.call", return_value=fake_resp) as m:
        t = g.create_topic("Alpha", paths=tmp_paths)
    assert t.id == 42
    m.assert_called_once()
    topics = g.list_topics(paths=tmp_paths)
    assert any(x.id == 42 for x in topics)


def test_create_topic_failure_raises(tmp_paths):
    _setup_forum(tmp_paths)
    with patch("metasphere.telegram.groups.tg_api.call",
               return_value={"ok": False, "description": "no perms"}):
        with pytest.raises(RuntimeError):
            g.create_topic("X", paths=tmp_paths)


def test_no_forum_configured(tmp_paths):
    with pytest.raises(RuntimeError):
        g.create_topic("X", paths=tmp_paths)


def test_send_to_topic_resolves_name(tmp_paths):
    _setup_forum(tmp_paths)
    with patch("metasphere.telegram.groups.tg_api.call",
               return_value={"ok": True, "result": {"message_thread_id": 7, "name": "T"}}):
        g.create_topic("T", paths=tmp_paths)
    with patch("metasphere.telegram.groups.tg_api.call",
               return_value={"ok": True, "result": {}}) as m:
        g.send_to_topic("T", "hello", paths=tmp_paths)
    kwargs = m.call_args.kwargs
    assert kwargs["message_thread_id"] == 7
    assert "hello" in kwargs["text"]


def test_topic_link_format(tmp_paths):
    _setup_forum(tmp_paths, "-1009999")
    with patch("metasphere.telegram.groups.tg_api.call",
               return_value={"ok": True, "result": {"message_thread_id": 5, "name": "L"}}):
        g.create_topic("L", paths=tmp_paths)
    link = g.topic_link("L", paths=tmp_paths)
    assert link == "https://t.me/c/9999/5"


def test_send_unknown_topic(tmp_paths):
    _setup_forum(tmp_paths)
    with pytest.raises(LookupError):
        g.send_to_topic("nope", "x", paths=tmp_paths)
