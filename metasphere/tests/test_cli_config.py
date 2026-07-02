"""Tests for ``metasphere config telegram``."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

from metasphere.cli import config as C


@pytest.fixture
def sandbox_config(tmp_path, monkeypatch):
    """Redirect the module's ``CONFIG_DIR`` + file paths into tmp_path."""
    root = tmp_path / ".metasphere"
    monkeypatch.setattr(C, "CONFIG_DIR", root / "config")
    monkeypatch.setattr(C, "TOKEN_ENV_FILE", root / "config" / "telegram.env")
    monkeypatch.setattr(C, "CHAT_ID_FILE", root / "config" / "telegram_chat_id")
    return root


def test_write_token_creates_env_file(sandbox_config):
    C._write_token("ABC:secret")
    content = C.TOKEN_ENV_FILE.read_text()
    assert "TELEGRAM_BOT_TOKEN=ABC:secret" in content


def test_write_chat_id(sandbox_config):
    C._write_chat_id(12345)
    assert C.CHAT_ID_FILE.read_text() == "12345"


def test_noninteractive_with_both_flags(sandbox_config, monkeypatch, capsys):
    monkeypatch.setattr(C, "_validate_token", lambda t: (True, "@testbot"))
    rc = C.main(["telegram", "--token", "T:1", "--chat-id", "42"])
    assert rc == 0
    assert C.TOKEN_ENV_FILE.read_text().startswith("TELEGRAM_BOT_TOKEN=T:1")
    assert C.CHAT_ID_FILE.read_text() == "42"
    out = capsys.readouterr().out
    assert "@testbot" in out


def test_noninteractive_invalid_token_returns_2(sandbox_config, monkeypatch, capsys):
    monkeypatch.setattr(C, "_validate_token",
                         lambda t: (False, "401 Unauthorized"))
    rc = C.main(["telegram", "--token", "bad"])
    assert rc == 2
    assert "401 Unauthorized" in capsys.readouterr().err
    # Token was NOT saved.
    assert not C.TOKEN_ENV_FILE.exists()


def test_interactive_happy_path(sandbox_config, monkeypatch, capsys):
    # Stub prompts: token, Enter-to-continue, pick #1 (single sender → auto).
    prompts = iter(["T:good", ""])
    monkeypatch.setattr(C, "_prompt", lambda msg: next(prompts))
    monkeypatch.setattr(C, "_validate_token", lambda t: (True, "@mybot"))
    monkeypatch.setattr(
        C, "_poll_for_chat_id",
        lambda timeout=30: [{"chat_id": 1234567890,
                              "name": "synthetic-user",
                              "last_text": "/start"}],
    )
    rc = C._interactive_flow()
    assert rc == 0
    assert C.TOKEN_ENV_FILE.read_text().startswith("TELEGRAM_BOT_TOKEN=T:good")
    assert C.CHAT_ID_FILE.read_text() == "1234567890"


def test_interactive_multiple_senders_pick_2(sandbox_config, monkeypatch):
    prompts = iter(["T:good", "", "2"])
    monkeypatch.setattr(C, "_prompt", lambda msg: next(prompts))
    monkeypatch.setattr(C, "_validate_token", lambda t: (True, "@mybot"))
    monkeypatch.setattr(C, "_poll_for_chat_id", lambda timeout=30: [
        {"chat_id": 100, "name": "first", "last_text": ""},
        {"chat_id": 200, "name": "second", "last_text": ""},
    ])
    rc = C._interactive_flow()
    assert rc == 0
    assert C.CHAT_ID_FILE.read_text() == "200"


def test_interactive_no_senders_falls_back_to_manual(sandbox_config, monkeypatch):
    prompts = iter(["T:good", "", "999"])
    monkeypatch.setattr(C, "_prompt", lambda msg: next(prompts))
    monkeypatch.setattr(C, "_validate_token", lambda t: (True, "@mybot"))
    monkeypatch.setattr(C, "_poll_for_chat_id", lambda timeout=30: [])
    rc = C._interactive_flow()
    assert rc == 0
    assert C.CHAT_ID_FILE.read_text() == "999"


def test_interactive_no_token_aborts(sandbox_config, monkeypatch, capsys):
    monkeypatch.setattr(C, "_prompt", lambda msg: "")
    rc = C._interactive_flow()
    assert rc == 2
    assert "no token" in capsys.readouterr().err
