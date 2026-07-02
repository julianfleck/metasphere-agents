"""Tests for ``metasphere.telegram.groups`` — forum topic management.

Covers ``create_topic`` against a configured forum id, ``list_topics``
persistence, and the topic-id mapping the gateway uses to route project
messages onto the right thread. Telegram API calls are mocked at
``tg_api.call``; no live network.
"""

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


# ---------------------------------------------------------------------------
# Non-interactive setup / verify
# ---------------------------------------------------------------------------


def _fake_call_factory(by_method):
    def _call(method, **kwargs):
        return by_method[method]
    return _call


def test_verify_forum_ok(tmp_paths):
    by_method = {
        "getChat": {"ok": True, "result": {
            "id": -100123, "type": "supergroup", "title": "Rho",
            "is_forum": True,
        }},
        "getMe": {"ok": True, "result": {"id": 999, "username": "bot"}},
        "getChatMember": {"ok": True, "result": {
            "status": "administrator", "can_manage_topics": True,
        }},
    }
    with patch("metasphere.telegram.groups.tg_api.call",
               side_effect=_fake_call_factory(by_method)):
        st = g.verify_forum("-100123", paths=tmp_paths)
    assert st.ok
    assert st.title == "Rho"
    assert st.bot_is_admin and st.can_manage_topics


def test_verify_forum_topics_disabled(tmp_paths):
    by_method = {
        "getChat": {"ok": True, "result": {
            "id": -100123, "type": "supergroup", "title": "Plain",
            "is_forum": False,
        }},
        "getMe": {"ok": True, "result": {"id": 999}},
        "getChatMember": {"ok": True, "result": {
            "status": "administrator", "can_manage_topics": True,
        }},
    }
    with patch("metasphere.telegram.groups.tg_api.call",
               side_effect=_fake_call_factory(by_method)):
        st = g.verify_forum("-100123", paths=tmp_paths)
    assert not st.ok
    assert "topics are not enabled" in st.describe_problem()


def test_verify_forum_bot_not_admin(tmp_paths):
    by_method = {
        "getChat": {"ok": True, "result": {
            "type": "supergroup", "title": "X", "is_forum": True,
        }},
        "getMe": {"ok": True, "result": {"id": 1}},
        "getChatMember": {"ok": True, "result": {"status": "member"}},
    }
    with patch("metasphere.telegram.groups.tg_api.call",
               side_effect=_fake_call_factory(by_method)):
        st = g.verify_forum("-100123", paths=tmp_paths)
    assert not st.ok
    assert "not an admin" in st.describe_problem()


def test_setup_forum_persists_when_valid(tmp_paths):
    by_method = {
        "getChat": {"ok": True, "result": {
            "type": "supergroup", "title": "Rho", "is_forum": True,
        }},
        "getMe": {"ok": True, "result": {"id": 1}},
        "getChatMember": {"ok": True, "result": {
            "status": "creator",
        }},
    }
    with patch("metasphere.telegram.groups.tg_api.call",
               side_effect=_fake_call_factory(by_method)):
        st = g.setup_forum("-100777", paths=tmp_paths)
    assert st.ok
    assert g.get_forum_id(tmp_paths) == "-100777"


def test_setup_forum_refuses_invalid_without_force(tmp_paths):
    by_method = {
        "getChat": {"ok": True, "result": {
            "type": "supergroup", "title": "X", "is_forum": False,
        }},
        "getMe": {"ok": True, "result": {"id": 1}},
        "getChatMember": {"ok": True, "result": {"status": "member"}},
    }
    with patch("metasphere.telegram.groups.tg_api.call",
               side_effect=_fake_call_factory(by_method)):
        with pytest.raises(RuntimeError):
            g.setup_forum("-100123", paths=tmp_paths)
    # nothing persisted
    assert g.get_forum_id(tmp_paths) is None


def test_create_topic_rejects_empty(tmp_paths):
    _setup_forum(tmp_paths)
    with pytest.raises(ValueError):
        g.create_topic("", paths=tmp_paths)


def test_create_topic_rejects_flag_shaped_name(tmp_paths):
    """A CLI typo like `groups create --help` must not call Telegram or persist."""
    _setup_forum(tmp_paths)
    with patch("metasphere.telegram.groups.tg_api.call") as m:
        with pytest.raises(ValueError):
            g.create_topic("--help", paths=tmp_paths)
    m.assert_not_called()
    assert g.list_topics(paths=tmp_paths) == []


def test_cli_create_help_does_not_create_topic(tmp_paths, monkeypatch):
    """`groups create --help` prints usage and does NOT hit the API."""
    from metasphere.cli import telegram_groups as cli
    monkeypatch.setattr("metasphere.cli.telegram_groups.resolve",
                        lambda: tmp_paths)
    _setup_forum(tmp_paths)
    with patch("metasphere.telegram.groups.tg_api.call") as m:
        rc = cli.main(["create", "--help"])
    assert rc == 0
    m.assert_not_called()
    assert g.list_topics(paths=tmp_paths) == []


def test_cli_create_flag_shaped_name_exits_clean(tmp_paths, monkeypatch, capsys):
    """A non-help flag-shaped name surfaces as a clean error, not an API call."""
    from metasphere.cli import telegram_groups as cli
    monkeypatch.setattr("metasphere.cli.telegram_groups.resolve",
                        lambda: tmp_paths)
    _setup_forum(tmp_paths)
    with patch("metasphere.telegram.groups.tg_api.call") as m:
        rc = cli.main(["create", "--force"])
    assert rc == 2
    m.assert_not_called()
    err = capsys.readouterr().err
    assert "looks like a CLI flag" in err


def test_cli_setup_non_interactive(tmp_paths, monkeypatch):
    from metasphere.cli import telegram_groups as cli
    monkeypatch.setattr("metasphere.cli.telegram_groups.resolve",
                        lambda: tmp_paths)
    by_method = {
        "getChat": {"ok": True, "result": {
            "type": "supergroup", "title": "T", "is_forum": True,
        }},
        "getMe": {"ok": True, "result": {"id": 1}},
        "getChatMember": {"ok": True, "result": {
            "status": "administrator", "can_manage_topics": True,
        }},
    }
    with patch("metasphere.telegram.groups.tg_api.call",
               side_effect=_fake_call_factory(by_method)):
        rc = cli.main(["setup", "--forum-id", "-100222"])
    assert rc == 0
    assert g.get_forum_id(tmp_paths) == "-100222"


@pytest.mark.parametrize("argv,extra", [
    (["list", "--bogus"], "--bogus"),
    (["list", "extra"], "extra"),
])
def test_cli_groups_list_rejects_unknown_args(tmp_paths, capsys, argv, extra):
    """``telegram groups list`` takes no arguments; pre-hardening
    silently dropped extras and printed the topic table anyway."""
    from metasphere.cli import telegram_groups as cli
    rc = cli.main(argv)
    _, err = capsys.readouterr()
    assert rc == 2
    assert "telegram groups list" in err
    assert extra in err
