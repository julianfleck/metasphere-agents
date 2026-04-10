"""Telegram integration tests (layer F)."""

from unittest.mock import patch

import pytest

from metasphere.project import (
    add_member,
    mirror_message_to_project_topic,
    new_project,
    wake_members,
)


def _setup_forum(tmp_paths, forum_id="-1001234567890"):
    tmp_paths.config.mkdir(parents=True, exist_ok=True)
    (tmp_paths.config / "telegram_forum_id").write_text(forum_id)


def test_new_project_creates_topic_when_forum_configured(tmp_paths, tmp_path):
    _setup_forum(tmp_paths)
    fake = {"ok": True, "result": {"message_thread_id": 99, "name": "proj"}}
    with patch("metasphere.telegram.groups.tg_api.call", return_value=fake):
        proj = new_project("proj", path=tmp_path / "proj", paths=tmp_paths)
    assert proj.telegram_topic is not None
    assert proj.telegram_topic["id"] == 99
    # Persisted in project.json
    from metasphere.project import load_project
    loaded = load_project(tmp_path / "proj")
    assert loaded.telegram_topic["id"] == 99


def test_new_project_no_topic_without_forum(tmp_paths, tmp_path):
    proj = new_project("bare", path=tmp_path / "bare", paths=tmp_paths)
    assert proj.telegram_topic is None


def test_mirror_message_when_in_project(tmp_paths, tmp_path):
    _setup_forum(tmp_paths)
    fake_create = {"ok": True, "result": {"message_thread_id": 42, "name": "p"}}
    with patch("metasphere.telegram.groups.tg_api.call", return_value=fake_create):
        new_project("p", path=tmp_paths.project_root / "p", paths=tmp_paths)
    calls = []
    fake_send = {"ok": True, "result": {}}
    def record(method, **kwargs):
        calls.append((method, kwargs))
        return fake_send
    with patch("metasphere.telegram.groups.tg_api.call", side_effect=record):
        topic_id = mirror_message_to_project_topic(
            tmp_paths.project_root / "p" / "sub", "!info", "hello",
            "@me", paths=tmp_paths,
        )
    assert topic_id == 42
    assert any(c[0] == "sendMessage" for c in calls)
    sm = [c for c in calls if c[0] == "sendMessage"][0]
    assert sm[1]["message_thread_id"] == 42
    assert "hello" in sm[1]["text"]


def test_mirror_message_noop_outside_project(tmp_paths, tmp_path):
    _setup_forum(tmp_paths)
    with patch("metasphere.telegram.groups.tg_api.call") as m:
        topic_id = mirror_message_to_project_topic(
            tmp_paths.project_root, "!info", "hi", "@me", paths=tmp_paths,
        )
    assert topic_id is None
    m.assert_not_called()


def test_mirror_message_noop_without_topic(tmp_paths, tmp_path):
    # Project exists but no topic (no forum configured at creation time).
    new_project("notopic", path=tmp_paths.project_root / "notopic", paths=tmp_paths)
    _setup_forum(tmp_paths)  # forum configured AFTER creation
    with patch("metasphere.telegram.groups.tg_api.call") as m:
        topic_id = mirror_message_to_project_topic(
            tmp_paths.project_root / "notopic", "!info", "hi", "@me", paths=tmp_paths,
        )
    assert topic_id is None
    m.assert_not_called()


def test_send_message_mirrors_additively(tmp_paths, tmp_path):
    _setup_forum(tmp_paths)
    fake_create = {"ok": True, "result": {"message_thread_id": 7, "name": "mp"}}
    with patch("metasphere.telegram.groups.tg_api.call", return_value=fake_create):
        new_project("mp", path=tmp_paths.project_root / "mp", paths=tmp_paths)

    from metasphere import messages as _m
    calls = []
    def record(method, **kwargs):
        calls.append((method, kwargs))
        return {"ok": True, "result": {}}
    with patch("metasphere.telegram.groups.tg_api.call", side_effect=record):
        msg = _m.send_message(
            target=f"@/mp", label="!info", body="body",
            from_agent="@me", paths=tmp_paths, wake=False,
        )
    # Fractal file was still written.
    assert msg.path is not None and msg.path.is_file()
    # Mirror fired.
    sends = [c for c in calls if c[0] == "sendMessage"]
    assert sends, "expected sendMessage call"
    assert sends[0][1]["message_thread_id"] == 7


def test_send_message_no_mirror_outside_project(tmp_paths, tmp_path):
    _setup_forum(tmp_paths)
    from metasphere import messages as _m
    with patch("metasphere.telegram.groups.tg_api.call") as m:
        _m.send_message(
            target="@.", label="!info", body="body",
            from_agent="@me", paths=tmp_paths, wake=False,
        )
    m.assert_not_called()


def test_wake_members_announces_on_topic(tmp_paths, tmp_path):
    _setup_forum(tmp_paths)
    fake_create = {"ok": True, "result": {"message_thread_id": 55, "name": "wp"}}
    with patch("metasphere.telegram.groups.tg_api.call", return_value=fake_create):
        new_project("wp", path=tmp_path / "wp", goal="ship", paths=tmp_paths)
    add_member("wp", "@lead", persistent=True, paths=tmp_paths)

    calls = []
    def record(method, **kwargs):
        calls.append((method, kwargs))
        return {"ok": True, "result": {}}
    with patch("metasphere.telegram.groups.tg_api.call", side_effect=record):
        waked = wake_members("wp", paths=tmp_paths, waker=lambda n, paths=None: None)
    assert waked == ["@lead"]
    announcements = [c for c in calls if c[0] == "sendMessage"]
    assert announcements
    assert announcements[0][1]["message_thread_id"] == 55
    assert "project waking" in announcements[0][1]["text"]


def test_cli_chat_routes_to_topic(tmp_paths, tmp_path, capsys):
    _setup_forum(tmp_paths)
    fake_create = {"ok": True, "result": {"message_thread_id": 101, "name": "cp"}}
    with patch("metasphere.telegram.groups.tg_api.call", return_value=fake_create):
        new_project("cp", path=tmp_path / "cp", paths=tmp_paths)

    from metasphere.cli.project import main as project_cli
    calls = []
    def record(method, **kwargs):
        calls.append((method, kwargs))
        return {"ok": True, "result": {}}
    with patch("metasphere.telegram.groups.tg_api.call", side_effect=record):
        rc = project_cli(["chat", "cp", "hello", "world"])
    assert rc == 0
    sends = [c for c in calls if c[0] == "sendMessage"]
    assert sends and sends[0][1]["message_thread_id"] == 101
    assert "hello world" in sends[0][1]["text"]
