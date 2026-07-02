"""Tests for ``metasphere.slack.routing`` — slash command config + resolver."""

from __future__ import annotations

import pytest

from metasphere.slack import routing as _routing


@pytest.fixture
def _cfg_dir(tmp_path, monkeypatch):
    cfg = tmp_path / "config"
    cfg.mkdir()
    monkeypatch.setattr(_routing, "CONFIG_DIR", str(cfg))
    return cfg


def _write_cfg(cfg_dir, body: str) -> None:
    (cfg_dir / _routing.COMMAND_CONFIG_BASENAME).write_text(body)


@pytest.fixture
def _roster(monkeypatch):
    """Stub the live roster with two wakeable persistent agents."""
    class _Rec:
        def __init__(self, name, persistent=True):
            self.name = name
            self.is_persistent = persistent

    monkeypatch.setattr(
        "metasphere.agents.list_agents",
        lambda *a, **k: [
            _Rec("@demo-agent"),
            _Rec("@other-agent"),
            _Rec("@ephemeral-thing", persistent=False),
        ],
    )


# --------------------------------------------------------------------------
# load_command_config
# --------------------------------------------------------------------------

def test_load_config_missing_file_is_empty(_cfg_dir):
    cfg = _routing.load_command_config()
    assert cfg == {"canonical_command": None, "literal": {}}


def test_load_config_parses_both_keys(_cfg_dir):
    _write_cfg(_cfg_dir, "canonical_command: ms\nliteral:\n  demo: '@demo-agent'\n")
    cfg = _routing.load_command_config()
    assert cfg["canonical_command"] == "ms"
    assert cfg["literal"] == {"demo": "@demo-agent"}


def test_load_config_normalises_agent_and_slash(_cfg_dir):
    _write_cfg(_cfg_dir, "canonical_command: /ms\nliteral:\n  /demo: demo-agent\n")
    cfg = _routing.load_command_config()
    assert cfg["canonical_command"] == "ms"  # leading slash stripped
    assert cfg["literal"] == {"demo": "@demo-agent"}  # @ added


def test_load_config_malformed_is_empty(_cfg_dir):
    _write_cfg(_cfg_dir, "this: [unclosed\n")
    assert _routing.load_command_config() == {"canonical_command": None, "literal": {}}


# --------------------------------------------------------------------------
# slash_resolver — literal mode
# --------------------------------------------------------------------------

def test_slash_literal_maps_full_text(_cfg_dir, _roster):
    _write_cfg(_cfg_dir, "literal:\n  demo: '@demo-agent'\n")
    target, text = _routing.slash_resolver(
        {"command": "/demo", "text": "what is the migration status?"}, "slack",
    )
    assert target == "@demo-agent"
    assert text == "what is the migration status?"


def test_slash_literal_unmapped_returns_none_with_message(_cfg_dir, _roster):
    _write_cfg(_cfg_dir, "literal:\n  demo: '@demo-agent'\n")
    target, text = _routing.slash_resolver({"command": "/unknown", "text": "x"}, "slack")
    assert target is None
    assert "no agent mapped for /unknown" in text


# --------------------------------------------------------------------------
# slash_resolver — canonical mode (/ms <agent> <request>)
# --------------------------------------------------------------------------

def test_slash_canonical_valid_agent_strips_selector(_cfg_dir, _roster):
    _write_cfg(_cfg_dir, "canonical_command: ms\n")
    target, text = _routing.slash_resolver(
        {"command": "/ms", "text": "demo-agent status of the replica?"}, "slack",
    )
    assert target == "@demo-agent"
    assert text == "status of the replica?"  # selector token stripped


def test_slash_canonical_accepts_at_prefixed_agent(_cfg_dir, _roster):
    _write_cfg(_cfg_dir, "canonical_command: ms\n")
    target, text = _routing.slash_resolver(
        {"command": "/ms", "text": "@other-agent draft intro"}, "slack",
    )
    assert target == "@other-agent"
    assert text == "draft intro"


def test_slash_canonical_unknown_agent_lists_roster(_cfg_dir, _roster):
    _write_cfg(_cfg_dir, "canonical_command: ms\n")
    target, text = _routing.slash_resolver(
        {"command": "/ms", "text": "nope do something"}, "slack",
    )
    assert target is None
    assert "Unknown agent '@nope'" in text
    assert "@demo-agent" in text and "@other-agent" in text


def test_slash_canonical_non_persistent_agent_rejected(_cfg_dir, _roster):
    """A non-wakeable (ephemeral) agent is not a valid target."""
    _write_cfg(_cfg_dir, "canonical_command: ms\n")
    target, _ = _routing.slash_resolver(
        {"command": "/ms", "text": "ephemeral-thing hi"}, "slack",
    )
    assert target is None


def test_slash_canonical_empty_shows_usage(_cfg_dir, _roster):
    _write_cfg(_cfg_dir, "canonical_command: ms\n")
    target, text = _routing.slash_resolver({"command": "/ms", "text": ""}, "slack")
    assert target is None
    assert "Usage: /ms <agent> <request>" in text


def test_slash_canonical_agent_only_empty_request(_cfg_dir, _roster):
    _write_cfg(_cfg_dir, "canonical_command: ms\n")
    target, text = _routing.slash_resolver(
        {"command": "/ms", "text": "demo-agent"}, "slack",
    )
    assert target == "@demo-agent"
    assert text == ""


# --------------------------------------------------------------------------
# slash_resolver — auto mode (zero-config: /<agent> -> @<agent>)
# --------------------------------------------------------------------------

def test_slash_auto_route_matches_agent_name_no_config(_cfg_dir, _roster):
    """No config at all: /demo-agent routes to @demo-agent, full text."""
    target, text = _routing.slash_resolver(
        {"command": "/demo-agent", "text": "summarise the latest run"}, "slack",
    )
    assert target == "@demo-agent"
    assert text == "summarise the latest run"


def test_slash_auto_route_unknown_name_unmapped(_cfg_dir, _roster):
    target, text = _routing.slash_resolver(
        {"command": "/nope", "text": "x"}, "slack",
    )
    assert target is None
    assert "no agent mapped for /nope" in text


def test_slash_auto_route_ignores_non_persistent(_cfg_dir, _roster):
    """An ephemeral (non-wakeable) agent name does not auto-route."""
    target, text = _routing.slash_resolver(
        {"command": "/ephemeral-thing", "text": "hi"}, "slack",
    )
    assert target is None
    assert "no agent mapped" in text


def test_slash_literal_overrides_auto(_cfg_dir, _roster):
    """An explicit literal alias wins over the auto agent-name match."""
    _write_cfg(_cfg_dir, "literal:\n  demo-agent: '@other-agent'\n")
    target, text = _routing.slash_resolver(
        {"command": "/demo-agent", "text": "do it"}, "slack",
    )
    assert target == "@other-agent"
    assert text == "do it"
