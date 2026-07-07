"""Tests for per-sender Telegram routing + access gate.

Two layers:
  1. ``routing.resolve_target`` — the config-driven decision (fail-open,
     id/username keying, enforce gating).
  2. ``handler.handle_update`` wiring — a mapped sender routes the inject
     to their agent's session; a denied sender gets the deny text and is
     NOT injected; the no-config default is byte-for-byte the historical
     orchestrator path.
"""

from __future__ import annotations

import pytest

from metasphere.telegram import handler, inject, poller, routing


# ─────────────────────────── resolve_target ───────────────────────────

def _write_cfg(tmp_path, body: str) -> str:
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / routing.ACCESS_CONFIG_BASENAME).write_text(body, encoding="utf-8")
    return str(cfg_dir)


def test_no_config_is_fail_open_to_orchestrator(tmp_path):
    cfg = str(tmp_path / "config")  # dir doesn't exist
    assert routing.resolve_target(555, "alice", config_dir=cfg) == (
        "@orchestrator", True, None,
    )


def test_mapped_by_numeric_id(tmp_path):
    cfg = _write_cfg(tmp_path, 'users:\n  "555": "@field-agent"\n')
    assert routing.resolve_target(555, "alice", config_dir=cfg) == (
        "@field-agent", True, None,
    )


def test_mapped_by_username_leading_at_and_case_insensitive(tmp_path):
    cfg = _write_cfg(tmp_path, "users:\n  alice: field-agent\n")
    # username carrying a leading @ and different case still matches
    assert routing.resolve_target(999, "@Alice", config_dir=cfg) == (
        "@field-agent", True, None,
    )


def test_numeric_id_takes_precedence_over_username(tmp_path):
    cfg = _write_cfg(
        tmp_path,
        'users:\n  "555": "@by-id"\n  alice: "@by-name"\n',
    )
    assert routing.resolve_target(555, "alice", config_dir=cfg)[0] == "@by-id"


def test_unmapped_enforce_false_falls_through_to_default(tmp_path):
    cfg = _write_cfg(tmp_path, 'users:\n  "1": "@x"\nenforce: false\n')
    assert routing.resolve_target(999, "nobody", config_dir=cfg) == (
        "@orchestrator", True, None,
    )


def test_unmapped_enforce_true_is_denied_with_default_message(tmp_path):
    cfg = _write_cfg(tmp_path, 'users:\n  "1": "@x"\nenforce: true\n')
    agent, allowed, deny = routing.resolve_target(999, "nobody", config_dir=cfg)
    assert agent == "@orchestrator"
    assert allowed is False
    assert deny and "access" in deny.lower()


def test_unmapped_enforce_true_custom_deny_message(tmp_path):
    cfg = _write_cfg(
        tmp_path,
        'enforce: true\ndeny_message: "kein zugang"\nusers:\n  "1": "@x"\n',
    )
    _, allowed, deny = routing.resolve_target(999, "nobody", config_dir=cfg)
    assert allowed is False
    assert deny == "kein zugang"


def test_custom_default_agent_for_unmapped(tmp_path):
    cfg = _write_cfg(tmp_path, "default_agent: house\nusers:\n  x: y\n")
    assert routing.resolve_target(999, "nobody", config_dir=cfg)[0] == "@house"


def test_malformed_yaml_is_fail_open(tmp_path):
    cfg = _write_cfg(tmp_path, "users: [this is not a mapping\n")
    assert routing.resolve_target(555, "alice", config_dir=cfg) == (
        "@orchestrator", True, None,
    )


def test_non_mapping_document_is_fail_open(tmp_path):
    cfg = _write_cfg(tmp_path, "- just\n- a\n- list\n")
    assert routing.resolve_target(555, "alice", config_dir=cfg) == (
        "@orchestrator", True, None,
    )


def test_agent_names_normalized_to_leading_at(tmp_path):
    cfg = _write_cfg(tmp_path, 'users:\n  "555": field-agent\n')  # no @
    assert routing.resolve_target(555, None, config_dir=cfg)[0] == "@field-agent"


# ─────────────────────────── handler wiring ───────────────────────────

@pytest.fixture
def _hermetic(monkeypatch, tmp_paths):
    """Neutralize the real session/tmux/network side effects of the inject
    path so the wiring tests observe only routing decisions."""
    monkeypatch.setattr(
        handler.api, "bot_identity",
        lambda: {"username": "testbot", "id": 1},
    )
    import metasphere.agents as _agents
    import metasphere.gateway.session as _gwsession
    monkeypatch.setattr(_gwsession, "start_session", lambda *a, **k: None)
    monkeypatch.setattr(_agents, "wake_persistent", lambda *a, **k: (None, True))


def _msg_update(chat_id: int, username: str, text: str = "hallo") -> poller.Update:
    return poller.Update.from_payload({
        "update_id": 1,
        "message": {
            "message_id": 10,
            "chat": {"id": chat_id, "type": "private"},
            "from": {"username": username},
            "text": text,
            "date": 0,
        },
    })


def _run(update, resolver):
    submits: list = []
    sends: list = []
    handler.handle_update(
        update,
        sender=lambda chat_id, text, **k: sends.append((chat_id, text)),
        reactor=lambda *a, **k: None,
        tmux_submit=lambda from_user, text, session=None, **k: (
            submits.append((from_user, text, session)) or True
        ),
        save_chat_id=lambda *a, **k: None,
        write_pending_ack=lambda *a, **k: None,
        resolve_target=resolver,
    )
    return submits, sends


def test_wiring_default_routes_to_orchestrator_session(_hermetic):
    submits, sends = _run(
        _msg_update(555, "alice"),
        lambda chat_id, username: ("@orchestrator", True, None),
    )
    assert len(submits) == 1
    assert submits[0][2] == inject.DEFAULT_SESSION
    assert sends == []  # no deny text on the allow path


def test_wiring_mapped_sender_routes_to_their_agent_session(_hermetic):
    submits, sends = _run(
        _msg_update(555, "alice"),
        lambda chat_id, username: ("@field-agent", True, None),
    )
    assert len(submits) == 1
    session = submits[0][2]
    assert session != inject.DEFAULT_SESSION
    assert "field-agent" in session


def test_wiring_denied_sender_gets_deny_text_and_no_inject(_hermetic):
    submits, sends = _run(
        _msg_update(999, "nobody"),
        lambda chat_id, username: ("@orchestrator", False, "kein zugang"),
    )
    assert submits == []            # gated: nothing injected
    assert sends == [(999, "kein zugang")]


def test_wiring_explicit_caller_target_is_not_overridden(_hermetic):
    """A caller that pins target_agent_id != default keeps it (routing only
    fills the default). resolver here would deny, but must not be consulted."""
    submits: list = []
    called = {"resolver": False}

    def _resolver(chat_id, username):
        called["resolver"] = True
        return "@orchestrator", False, "denied"

    handler.handle_update(
        _msg_update(999, "nobody"),
        sender=lambda *a, **k: None,
        reactor=lambda *a, **k: None,
        tmux_submit=lambda from_user, text, session=None, **k: (
            submits.append(session) or True
        ),
        save_chat_id=lambda *a, **k: None,
        write_pending_ack=lambda *a, **k: None,
        resolve_target=_resolver,
        target_agent_id="@field-agent",
    )
    assert called["resolver"] is False
    assert len(submits) == 1
    assert "field-agent" in submits[0]
