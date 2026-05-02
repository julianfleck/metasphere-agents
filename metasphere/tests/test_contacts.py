"""Tests for metasphere.contacts (unified ADDRESSBOOK.yaml loader)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from metasphere import contacts as _contacts


@pytest.fixture(autouse=True)
def _isolate_cache():
    """Reset the lru_cache + WARN flags between tests."""
    _contacts.clear_cache()
    yield
    _contacts.clear_cache()


# ---------- happy-path YAML ----------

def test_load_addressbook_yaml(tmp_paths):
    ab = tmp_paths.root / "ADDRESSBOOK.yaml"
    ab.write_text(
        "contacts:\n"
        "  alpha:\n"
        "    telegram: 1111\n"
        "  Beta:\n"
        "    telegram: 2222\n"
        "    email: beta@example.com\n"
    )
    out = _contacts.load_addressbook(tmp_paths)
    assert out == {
        "alpha": {"telegram": 1111},
        "beta": {"telegram": 2222, "email": "beta@example.com"},
    }


def test_lookup_telegram_returns_int(tmp_paths):
    ab = tmp_paths.root / "ADDRESSBOOK.yaml"
    ab.write_text(
        "contacts:\n"
        "  alpha:\n"
        "    telegram: 1111\n"
    )
    assert _contacts.lookup_telegram("alpha", tmp_paths) == 1111
    # Case-insensitive
    assert _contacts.lookup_telegram("ALPHA", tmp_paths) == 1111


def test_lookup_telegram_unknown_contact(tmp_paths):
    (tmp_paths.root / "ADDRESSBOOK.yaml").write_text("contacts: {}\n")
    assert _contacts.lookup_telegram("nope", tmp_paths) is None


def test_has_contact(tmp_paths):
    (tmp_paths.root / "ADDRESSBOOK.yaml").write_text(
        "contacts:\n"
        "  alpha:\n"
        "    email: a@b.com\n"
    )
    assert _contacts.has_contact("alpha", tmp_paths) is True
    assert _contacts.has_contact("ALPHA", tmp_paths) is True
    assert _contacts.has_contact("missing", tmp_paths) is False


def test_lookup_telegram_for_contact_without_telegram_method(tmp_paths):
    """Contact exists but has no telegram method → returns None."""
    (tmp_paths.root / "ADDRESSBOOK.yaml").write_text(
        "contacts:\n"
        "  alpha:\n"
        "    email: a@b.com\n"
    )
    # has_contact True
    assert _contacts.has_contact("alpha", tmp_paths) is True
    # but lookup_telegram None
    assert _contacts.lookup_telegram("alpha", tmp_paths) is None


# ---------- missing files ----------

def test_load_addressbook_missing_file_returns_empty(tmp_paths, capsys):
    """No ADDRESSBOOK.yaml + no legacy → empty dict + WARN."""
    out = _contacts.load_addressbook(tmp_paths)
    assert out == {}
    captured = capsys.readouterr()
    assert "ADDRESSBOOK.yaml" in captured.err
    assert "not found" in captured.err


def test_load_addressbook_missing_does_not_crash(tmp_paths):
    """Stranger install: just returns empty, no exception."""
    # Repeated calls are also safe.
    for _ in range(3):
        assert _contacts.load_addressbook(tmp_paths) == {}


# ---------- malformed YAML ----------

def test_load_addressbook_malformed_yaml(tmp_paths, capsys):
    ab = tmp_paths.root / "ADDRESSBOOK.yaml"
    ab.write_text("contacts:\n  alpha: [unbalanced bracket\n")
    out = _contacts.load_addressbook(tmp_paths)
    assert out == {}
    captured = capsys.readouterr()
    assert "malformed YAML" in captured.err


def test_load_addressbook_contacts_must_be_mapping(tmp_paths, capsys):
    """``contacts`` must be a dict, not a list."""
    (tmp_paths.root / "ADDRESSBOOK.yaml").write_text(
        "contacts:\n  - alpha\n  - beta\n"
    )
    out = _contacts.load_addressbook(tmp_paths)
    assert out == {}
    captured = capsys.readouterr()
    assert "must be a mapping" in captured.err


def test_load_addressbook_skips_non_dict_entries(tmp_paths):
    """A contact whose value is not a mapping is silently skipped."""
    (tmp_paths.root / "ADDRESSBOOK.yaml").write_text(
        "contacts:\n"
        "  alpha:\n"
        "    telegram: 1111\n"
        "  bad: \"this is a string not a dict\"\n"
    )
    out = _contacts.load_addressbook(tmp_paths)
    assert "alpha" in out
    assert "bad" not in out


# ---------- legacy fallback ----------

def test_legacy_fallback_when_yaml_missing(tmp_paths, capsys):
    """No YAML, has legacy JSON → loads legacy with deprecation WARN."""
    legacy = tmp_paths.root / "config" / "telegram_contacts.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps({"alpha": 9999, "Beta": 8888}))

    out = _contacts.load_addressbook(tmp_paths)
    assert out == {
        "alpha": {"telegram": 9999},
        "beta": {"telegram": 8888},
    }
    captured = capsys.readouterr()
    assert "deprecated" in captured.err
    assert "telegram_contacts.json" in captured.err


def test_yaml_wins_over_legacy(tmp_paths, capsys):
    """Both files present → YAML used, no deprecation WARN fires."""
    (tmp_paths.root / "ADDRESSBOOK.yaml").write_text(
        "contacts:\n"
        "  alpha:\n"
        "    telegram: 1111\n"
    )
    legacy = tmp_paths.root / "config" / "telegram_contacts.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps({"alpha": 9999}))

    out = _contacts.load_addressbook(tmp_paths)
    assert out == {"alpha": {"telegram": 1111}}
    captured = capsys.readouterr()
    assert "deprecated" not in captured.err


def test_legacy_warn_emits_only_once(tmp_paths, capsys):
    """Second load_addressbook call from same process should not re-warn."""
    legacy = tmp_paths.root / "config" / "telegram_contacts.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps({"alpha": 1111}))

    _contacts.load_addressbook(tmp_paths)
    capsys.readouterr()  # discard first WARN
    # Reset cache to force a re-load (but WARN flag stays).
    _contacts._load_cached.cache_clear()
    _contacts.load_addressbook(tmp_paths)
    captured = capsys.readouterr()
    assert captured.err == ""


# ---------- default_telegram_chat_id ----------

def test_default_recipient_yaml_resolves(tmp_paths):
    """`default-recipient: alpha` → alpha's telegram chat id."""
    (tmp_paths.root / "ADDRESSBOOK.yaml").write_text(
        "default-recipient: alpha\n"
        "contacts:\n"
        "  alpha:\n"
        "    telegram: 1111\n"
        "  beta:\n"
        "    telegram: 2222\n"
    )
    assert _contacts.default_telegram_chat_id(tmp_paths) == 1111


def test_default_recipient_case_insensitive(tmp_paths):
    """default-recipient matches contact name case-insensitively."""
    (tmp_paths.root / "ADDRESSBOOK.yaml").write_text(
        "default-recipient: Alpha\n"
        "contacts:\n"
        "  alpha:\n"
        "    telegram: 1111\n"
    )
    assert _contacts.default_telegram_chat_id(tmp_paths) == 1111


def test_default_recipient_no_config_returns_none(tmp_paths):
    """No YAML, no legacy → no fallback. Caller must error rather
    than silently substituting a last-inbound chat id."""
    assert _contacts.default_telegram_chat_id(tmp_paths) is None


def test_default_recipient_unset_returns_none(tmp_paths):
    """YAML exists with contacts but no `default-recipient` key →
    None. Stranger installs without a configured main user must
    error loudly rather than guessing."""
    (tmp_paths.root / "ADDRESSBOOK.yaml").write_text(
        "contacts:\n"
        "  alpha:\n"
        "    telegram: 1111\n"
    )
    assert _contacts.default_telegram_chat_id(tmp_paths) is None


def test_default_recipient_points_at_unknown_contact_returns_none(tmp_paths):
    """`default-recipient: ghost` with ghost not in contacts → None.
    No silent fall-through; the operator's intent (ghost) is honored
    and the unresolved name surfaces as a loud CLI error."""
    (tmp_paths.root / "ADDRESSBOOK.yaml").write_text(
        "default-recipient: ghost\n"
        "contacts:\n"
        "  alpha:\n"
        "    telegram: 1111\n"
    )
    assert _contacts.default_telegram_chat_id(tmp_paths) is None


def test_default_recipient_legacy_json_only_returns_none(tmp_paths):
    """Legacy JSON has no concept of default-recipient. Even with
    contacts present in the legacy file, `default_telegram_chat_id`
    returns None — operators migrate via ``install.sh``, which writes
    a `default-recipient` pointer when one of the legacy file's
    entries matches the migration convention. Pre-migration call
    paths must error rather than guess."""
    legacy = tmp_paths.root / "config" / "telegram_contacts.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps({"alice": 1111, "bob": 2222}))
    assert _contacts.default_telegram_chat_id(tmp_paths) is None
