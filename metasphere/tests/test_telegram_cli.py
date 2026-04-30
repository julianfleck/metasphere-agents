"""Tests for ``metasphere.cli.telegram`` — the @<name> shorthand,
positional-shape validation, and addressbook-driven contact resolution.

Network-side delivery is monkey-patched out so these are pure CLI
parser + addressbook tests; the actual ``api.send_with_cc`` flow has
its own coverage in ``test_telegram.py``.
"""

from __future__ import annotations

import json

import pytest

from metasphere import contacts as _contacts
from metasphere.cli import telegram as _cli


@pytest.fixture(autouse=True)
def _isolate_contacts_cache():
    _contacts.clear_cache()
    yield
    _contacts.clear_cache()


@pytest.fixture
def stub_send(monkeypatch):
    """Stub api.send_with_cc + archiver.archive_outgoing + posthook
    marker so tests focus on the CLI plumbing, not delivery."""
    sent: list[tuple[int, str]] = []

    def fake_send(chat_id, text, *args, **kwargs):
        sent.append((chat_id, text))
        return {"ok": True, "result": {"message_id": 1}}

    monkeypatch.setattr(_cli.api, "send_with_cc", fake_send)
    monkeypatch.setattr(_cli.archiver, "archive_outgoing", lambda *a, **k: None)
    # mark_orchestrator_explicit_send is best-effort — stub it to a no-op
    import metasphere.posthook
    monkeypatch.setattr(metasphere.posthook, "mark_orchestrator_explicit_send",
                        lambda *a, **k: None)
    return sent


@pytest.fixture
def addressbook(tmp_paths, monkeypatch):
    """Point ``contacts._addressbook_path`` at tmp_paths and write a
    standard addressbook fixture."""
    ab = tmp_paths.root / "ADDRESSBOOK.yaml"
    ab.write_text(
        "contacts:\n"
        "  alpha:\n"
        "    telegram: 1111\n"
        "  beta:\n"
        "    email: beta@example.com\n"
    )

    def _ab_path(_paths=None):
        return ab

    def _legacy_path(_paths=None):
        return tmp_paths.root / "config" / "telegram_contacts.json"

    monkeypatch.setattr(_contacts, "_addressbook_path", _ab_path)
    monkeypatch.setattr(_contacts, "_legacy_contacts_path", _legacy_path)
    return ab


# ---------- _parse_send_positionals ----------

def test_parse_single_positional_text():
    to, text, err = _cli._parse_send_positionals(["hello world"])
    assert to is None
    assert text == "hello world"
    assert err is None


def test_parse_at_name_shorthand():
    to, text, err = _cli._parse_send_positionals(["@alpha", "hello"])
    assert to == "alpha"
    assert text == "hello"
    assert err is None


def test_parse_at_name_with_no_text_errors():
    to, text, err = _cli._parse_send_positionals(["@alpha"])
    assert err is not None
    assert "no message text" in err.lower()


def test_parse_bare_at_errors():
    to, text, err = _cli._parse_send_positionals(["@", "hello"])
    assert err is not None
    assert "empty contact name" in err


def test_parse_two_positionals_no_at_errors():
    """Plain ``send "hello" "world"`` is the bug case from the brief."""
    to, text, err = _cli._parse_send_positionals(["hello", "world"])
    assert err is not None
    assert "too many positionals" in err
    assert "--to" in err  # actionable hint


def test_parse_at_name_with_extra_positionals_errors():
    to, text, err = _cli._parse_send_positionals(["@alpha", "hello", "extra"])
    assert err is not None
    assert "too many positionals after '@alpha'" in err


def test_parse_empty_positionals_errors():
    to, text, err = _cli._parse_send_positionals([])
    assert err is not None


# ---------- end-to-end via build_parser + cmd_send ----------

def test_send_at_name_resolves_via_addressbook(addressbook, stub_send,
                                                monkeypatch):
    """``send "@alpha" "msg"`` looks up alpha → 1111 and dispatches."""
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@orchestrator")
    rc = _cli.main(["send", "@alpha", "ping"])
    assert rc == 0
    assert stub_send == [(1111, "ping")]


def test_send_at_unknown_contact_errors(addressbook, stub_send,
                                         monkeypatch, capsys):
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@orchestrator")
    rc = _cli.main(["send", "@nope", "ping"])
    assert rc == 2
    assert stub_send == []
    err = capsys.readouterr().err
    assert "'nope' not in" in err
    assert "ADDRESSBOOK.yaml" in err


def test_send_at_contact_without_telegram_method_errors(addressbook, stub_send,
                                                         monkeypatch, capsys):
    """beta has email but no telegram → distinct error."""
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@orchestrator")
    rc = _cli.main(["send", "@beta", "ping"])
    assert rc == 2
    assert stub_send == []
    err = capsys.readouterr().err
    assert "has no telegram entry" in err
    assert "contacts.beta.telegram" in err


def test_send_two_positionals_no_at_actionable_error(addressbook, stub_send,
                                                      monkeypatch, capsys):
    """The original bug: ``send "@<name>" "msg"`` was the agent's
    intent but the code-side ``send "alpha" "msg"`` (no @) is the
    next-most-likely shape. Both should fail loudly with usage hints."""
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@orchestrator")
    rc = _cli.main(["send", "alpha", "ping"])
    assert rc == 2
    assert stub_send == []
    err = capsys.readouterr().err
    assert "too many positionals" in err
    assert "Did you mean" in err


def test_send_at_name_with_extra_positional_errors(addressbook, stub_send,
                                                    monkeypatch, capsys):
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@orchestrator")
    rc = _cli.main(["send", "@alpha", "ping", "extra"])
    assert rc == 2
    assert stub_send == []
    err = capsys.readouterr().err
    assert "too many positionals after '@alpha'" in err


def test_send_explicit_chat_id_still_works(addressbook, stub_send, monkeypatch):
    """--chat-id still bypasses addressbook lookup — used by callers
    that already have the chat id resolved."""
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@orchestrator")
    rc = _cli.main(["send", "msg-text", "--chat-id", "5555"])
    assert rc == 0
    assert stub_send == [(5555, "msg-text")]


def test_send_explicit_to_still_works(addressbook, stub_send, monkeypatch):
    """--to alpha resolves via addressbook same as @alpha shorthand."""
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@orchestrator")
    rc = _cli.main(["send", "msg", "--to", "alpha"])
    assert rc == 0
    assert stub_send == [(1111, "msg")]


# ---------- legacy fallback ----------

def test_send_at_name_falls_back_to_legacy_contacts(tmp_paths, stub_send,
                                                     monkeypatch, capsys):
    """No ADDRESSBOOK.yaml + has legacy JSON → resolves via legacy."""
    legacy = tmp_paths.root / "config" / "telegram_contacts.json"
    legacy.parent.mkdir(parents=True, exist_ok=True)
    legacy.write_text(json.dumps({"gamma": 7777}))

    def _ab_path(_paths=None):
        return tmp_paths.root / "ADDRESSBOOK.yaml"

    def _legacy_path(_paths=None):
        return legacy

    monkeypatch.setattr(_contacts, "_addressbook_path", _ab_path)
    monkeypatch.setattr(_contacts, "_legacy_contacts_path", _legacy_path)
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@orchestrator")

    rc = _cli.main(["send", "@gamma", "ping"])
    assert rc == 0
    assert stub_send == [(7777, "ping")]
    # Deprecation WARN fires once on legacy load.
    err = capsys.readouterr().err
    assert "deprecated" in err
