"""Tests for ``metasphere.routing.active`` — active conversation pin."""

from __future__ import annotations

import json
from pathlib import Path

from metasphere.paths import Paths
from metasphere.routing.active import (
    ACTIVE_CONVERSATION_BASENAME,
    get_active_conversation,
    set_active_conversation,
)


def test_set_and_get_active_conversation_roundtrip(tmp_paths: Paths):
    set_active_conversation(
        "@orchestrator", "telegram", 228838013, tmp_paths,
    )
    pin = get_active_conversation("@orchestrator", tmp_paths)
    assert pin is not None
    assert pin["surface_id"] == "telegram"
    assert pin["chat_id"] == "228838013"
    assert isinstance(pin["ts"], float)


def test_get_active_conversation_missing_returns_none(tmp_paths: Paths):
    assert get_active_conversation("@nobody", tmp_paths) is None


def test_set_active_conversation_overwrites_atomically(tmp_paths: Paths):
    set_active_conversation(
        "@orchestrator", "telegram", 1, tmp_paths,
    )
    set_active_conversation(
        "@orchestrator", "slack-relay", "C12345", tmp_paths,
    )
    pin = get_active_conversation("@orchestrator", tmp_paths)
    assert pin is not None
    assert pin["surface_id"] == "slack-relay"
    assert pin["chat_id"] == "C12345"


def test_get_active_conversation_malformed_returns_none(tmp_paths: Paths):
    """A corrupt pointer file → None (defensive; falls back to legacy)."""
    agent_dir = tmp_paths.agent_dir("@orchestrator")
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / ACTIVE_CONVERSATION_BASENAME).write_text(
        "not json at all", encoding="utf-8",
    )
    assert get_active_conversation("@orchestrator", tmp_paths) is None


def test_set_active_conversation_seeds_dir(tmp_paths: Paths):
    """A fresh agent that has never been seeded still gets a pointer."""
    set_active_conversation(
        "@fresh", "telegram", 42, tmp_paths,
    )
    pin = get_active_conversation("@fresh", tmp_paths)
    assert pin is not None
    # The pin must round-trip through the JSON file (not a stale memo).
    target = tmp_paths.agent_dir("@fresh") / ACTIVE_CONVERSATION_BASENAME
    assert target.is_file()
    data = json.loads(target.read_text(encoding="utf-8"))
    assert data["surface_id"] == "telegram"
    assert data["chat_id"] == "42"
