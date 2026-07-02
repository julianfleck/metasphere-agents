"""Tests for B2 — msg read/discovery surface for cross-scope sends.

Regression set: ``metasphere msg read msg-XXX`` failing for messages
that exist on disk but have moved out of the live ``inbox/`` (or
never landed in any inbox at all — only sender's outbox).

Repro (2026-05-29): a lead→eng dispatch was sent, processed,
completed, and archived ~2h later. The subsequent ``msg read
<msg-id>`` returned "not found" despite the archived file existing
on disk — discovery walked inbox dirs only and the index pointed to
the now-gone inbox path.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from metasphere import messages as m
from metasphere.paths import Paths


def _send(tmp_paths: Paths, body: str = "hello") -> m.Message:
    return m.send_message(
        "@..", "!info", body, "@sender",
        paths=tmp_paths, wake=False,
    )


# ---------------------------------------------------------------------------
# Live inbox case — confirm no regression on the common path.
# ---------------------------------------------------------------------------


def test_mark_read_resolves_live_inbox(tmp_paths: Paths):
    msg = _send(tmp_paths, "live body")
    out = m.mark_read(msg.id, paths=tmp_paths)
    assert out.id == msg.id
    assert "live body" in out.body


def test_find_message_anywhere_resolves_live_inbox(tmp_paths: Paths):
    msg = _send(tmp_paths)
    hit = m._find_message_anywhere(msg.id, paths=tmp_paths)
    assert hit is not None
    assert hit.parent.name == "inbox"


# ---------------------------------------------------------------------------
# Archive case — the today-repro scenario.
# ---------------------------------------------------------------------------


def test_mark_read_resolves_archived_message(tmp_paths: Paths):
    """The today-repro: a message that was archived after completion
    must still be readable by id."""
    msg = _send(tmp_paths, "archived body")
    assert msg.path is not None
    archived = m.archive_message(msg.path)
    assert archived.parent.parent.name == "archive"
    assert not msg.path.exists(), "inbox copy should have been moved"

    out = m.mark_read(msg.id, paths=tmp_paths)
    assert out.id == msg.id
    assert "archived body" in out.body


def test_mark_read_does_not_mutate_archived_message(tmp_paths: Paths):
    """Archive-located copies are read-only — the read_at/status
    promotion logic must not fire on them."""
    msg = _send(tmp_paths, "preserved body")
    assert msg.path is not None
    archived = m.archive_message(msg.path)
    pre_text = archived.read_text(encoding="utf-8")

    m.mark_read(msg.id, paths=tmp_paths)

    assert archived.read_text(encoding="utf-8") == pre_text, (
        "mark_read mutated the archived copy"
    )


def test_archive_message_updates_index(tmp_paths: Paths):
    """The index must follow the file when it moves — otherwise the
    fast-path lookup is stuck pointing at the now-empty inbox slot."""
    msg = _send(tmp_paths)
    indexed_pre = m._index_lookup(msg.id, tmp_paths)
    assert indexed_pre is not None
    assert indexed_pre.parent.name == "inbox"

    archived = m.archive_message(msg.path)

    indexed_post = m._index_lookup(msg.id, tmp_paths)
    assert indexed_post == archived


# ---------------------------------------------------------------------------
# Outbox-only case — sender-side query when the inbox copy never landed.
# ---------------------------------------------------------------------------


def test_mark_read_resolves_outbox_only(tmp_paths: Paths):
    """When only the sender's outbox copy exists (degenerate: inbox
    write was lost), ``msg read`` still resolves so the operator can
    inspect what was actually sent."""
    msg = _send(tmp_paths, "outbox-only body")
    assert msg.path is not None
    # Simulate a delivery glitch: inbox copy disappears, outbox stays.
    msg.path.unlink()
    # The index hit is stale and now skipped (file doesn't exist).
    # The walk has to find the outbox copy.

    out = m.mark_read(msg.id, paths=tmp_paths)
    assert "outbox-only body" in out.body


def test_mark_read_does_not_mutate_outbox_copy(tmp_paths: Paths):
    """Outbox is sender-owned; a recipient-side mark_read mutating it
    would invert ownership semantics."""
    msg = _send(tmp_paths, "outbox preserved")
    assert msg.path is not None
    msg.path.unlink()
    outbox_path = (
        tmp_paths.projects / "testproj" / ".messages" / "outbox"
        / f"{msg.id}.msg"
    )
    pre_text = outbox_path.read_text(encoding="utf-8")

    m.mark_read(msg.id, paths=tmp_paths)

    assert outbox_path.read_text(encoding="utf-8") == pre_text


# ---------------------------------------------------------------------------
# Index staleness regression — the brief's hypothesis (3).
# ---------------------------------------------------------------------------


def test_stale_index_entry_falls_through_to_walk(tmp_paths: Paths):
    """Index points at a path whose file is gone. The lookup must fall
    through to the walk, find the actual location, and return cleanly
    — never raise FileNotFoundError downstream."""
    msg = _send(tmp_paths, "stale-index body")
    archived = m.archive_message(msg.path)

    # Corrupt the index back to the now-empty inbox path.
    import json
    idx_path = m._index_path(tmp_paths)
    idx = json.loads(idx_path.read_text())
    idx[msg.id] = str(msg.path)  # original inbox path, now gone
    idx_path.write_text(json.dumps(idx))

    out = m.mark_read(msg.id, paths=tmp_paths)
    assert out.id == msg.id
    assert "stale-index body" in out.body


def test_truly_missing_message_raises_clean_not_found(tmp_paths: Paths):
    """No file anywhere → FileNotFoundError with the canonical message,
    not a downstream OSError leaking through."""
    with pytest.raises(FileNotFoundError, match="msg-bogus-12345 not found"):
        m.mark_read("msg-bogus-12345", paths=tmp_paths)


# ---------------------------------------------------------------------------
# Write-side surfaces must keep operating on live inbox only.
# ---------------------------------------------------------------------------


def test_find_inbox_msg_skips_archived(tmp_paths: Paths):
    """``_find_inbox_msg`` is used by ``reply_to`` / ``mark_done`` —
    they must not pick up an archived path because the write would
    mutate already-completed state."""
    msg = _send(tmp_paths)
    m.archive_message(msg.path)

    hit = m._find_inbox_msg(msg.id, tmp_paths.project_root, paths=tmp_paths)
    assert hit is None, (
        "_find_inbox_msg returned an archived path — write-side "
        "surfaces would mutate completed state"
    )
