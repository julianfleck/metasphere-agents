"""Tests for metasphere.messages — the Python port of scripts/messages.

Specifically locks down the pieces the bash version was unsafe about:
ordered frontmatter round-trips, field updates under flock, and parent
scope walking.
"""

from __future__ import annotations

import multiprocessing as mp
import os
from pathlib import Path

import pytest

from metasphere import messages as m
from metasphere.io import read_frontmatter_file


# ---------------------------------------------------------------------------
# Roundtrip
# ---------------------------------------------------------------------------


def test_send_and_read_roundtrip(tmp_paths):
    msg = m.send_message(
        "@..", "!info", "hello there", "@child", paths=tmp_paths, wake=False
    )
    assert msg.id.startswith("msg-")
    assert msg.path is not None and msg.path.exists()

    loaded = m.read_message(msg.path)
    assert loaded.id == msg.id
    assert loaded.from_ == "@child"
    assert loaded.to == "@.."
    assert loaded.label == "!info"
    assert loaded.status == m.STATUS_UNREAD
    assert "hello there" in loaded.body

    # Outbox copy exists too
    outbox = tmp_paths.scope / ".messages" / "outbox" / f"{msg.id}.msg"
    assert outbox.exists()


# ---------------------------------------------------------------------------
# Inbox walking
# ---------------------------------------------------------------------------


def test_collect_inbox_walks_parent_scopes(tmp_paths, monkeypatch):
    repo = tmp_paths.repo
    child_scope = repo / "sub" / "deep"
    child_scope.mkdir(parents=True)

    # Drop a message at repo root, one at mid, one at deep
    for scope_dir, label in [
        (repo, "!root"),
        (repo / "sub", "!mid"),
        (child_scope, "!deep"),
    ]:
        inbox = scope_dir / ".messages" / "inbox"
        inbox.mkdir(parents=True, exist_ok=True)
        msg = m.Message(
            id=f"msg-{label.strip('!')}",
            from_="@sender",
            to="@.",
            label=label,
            status=m.STATUS_UNREAD,
            scope="/",
            created="2026-04-07T00:00:00Z",
            body="\nbody\n",
        )
        m.write_message(msg, inbox / f"{msg.id}.msg")

    msgs = m.collect_inbox(child_scope, repo)
    labels = {x.label for x in msgs}
    assert labels == {"!root", "!mid", "!deep"}

    # Mid scope sees root + mid but NOT deep (upward only)
    msgs_mid = m.collect_inbox(repo / "sub", repo)
    assert {x.label for x in msgs_mid} == {"!root", "!mid"}


def test_send_to_absolute_path_target(tmp_paths, tmp_path):
    """@/abs/path/ resolves to absolute filesystem path, not repo-joined.

    Regression: previously ``@/tmp/foo/`` was joined to scope/repo_root,
    producing ``<repo_root>/tmp/foo`` and doubled-prefix paths.
    """
    abs_target = tmp_path / "elsewhere" / "scope"
    abs_target.mkdir(parents=True)

    msg = m.send_message(
        f"@/{abs_target}/",
        "!info",
        "absolute target",
        "@sender",
        paths=tmp_paths,
        wake=False,
    )
    expected_inbox = abs_target / ".messages" / "inbox" / f"{msg.id}.msg"
    assert expected_inbox.exists(), f"message not in abs inbox: {expected_inbox}"
    # Must NOT have been written under repo_root.
    doubled = tmp_paths.repo / str(abs_target).lstrip("/")
    assert not (doubled / ".messages" / "inbox" / f"{msg.id}.msg").exists()


# ---------------------------------------------------------------------------
# Frontmatter integrity
# ---------------------------------------------------------------------------


def test_update_status_preserves_ordering_and_body(tmp_paths):
    msg = m.send_message(
        "@..", "!task", "do the thing\nwith newlines", "@a", paths=tmp_paths, wake=False
    )
    p = msg.path

    # Snapshot field order before
    fm_before = read_frontmatter_file(p)
    keys_before = list(fm_before.meta.keys())
    body_before = fm_before.body

    m.update_status(p, "status", m.STATUS_READ)
    m.update_status(p, "read_at", "2026-04-07T12:00:00Z")

    fm_after = read_frontmatter_file(p)
    assert list(fm_after.meta.keys()) == keys_before, "field order changed"
    assert fm_after.meta["status"] == m.STATUS_READ
    assert fm_after.meta["read_at"] == "2026-04-07T12:00:00Z"
    # Other fields survived intact
    assert fm_after.meta["id"] == msg.id
    assert fm_after.meta["label"] == "!task"
    # Body unchanged (modulo leading newline normalization)
    assert fm_after.body.strip() == body_before.strip()
    assert "do the thing" in fm_after.body


# ---------------------------------------------------------------------------
# Locking under concurrent writers
# ---------------------------------------------------------------------------


def _hammer(path_str: str, field_name: str, value: str, n: int) -> None:
    # Each child repeatedly sets the same field. With flock, the file
    # must always be parseable and land in a coherent final state.
    from metasphere import messages as mm
    p = Path(path_str)
    for _ in range(n):
        mm.update_status(p, field_name, value)


def test_file_lock_prevents_interleaved_writes(tmp_paths):
    msg = m.send_message(
        "@..", "!task", "concurrent", "@a", paths=tmp_paths, wake=False
    )
    p = str(msg.path)

    procs = [
        mp.Process(target=_hammer, args=(p, "status", "read", 30)),
        mp.Process(target=_hammer, args=(p, "read_at", "2026-04-07T00:00:00Z", 30)),
    ]
    for pr in procs:
        pr.start()
    for pr in procs:
        pr.join(timeout=30)
        assert pr.exitcode == 0, "hammer process crashed (likely torn write)"

    # Final state must still be a valid, fully-populated message file
    loaded = m.read_message(Path(p))
    assert loaded.id == msg.id
    assert loaded.status == "read"
    assert loaded.read_at == "2026-04-07T00:00:00Z"
    assert loaded.from_ == "@a"  # untouched fields survived


# ---------------------------------------------------------------------------
# Reply flow
# ---------------------------------------------------------------------------


def test_reply_marks_original_and_sets_reply_to(tmp_paths):
    # Original message lands in the scope inbox, as if a peer sent it.
    orig = m.send_message(
        "@.", "!query", "how do?", "@other", paths=tmp_paths, wake=False
    )
    assert orig.path is not None and orig.path.exists()

    reply = m.reply_to_message(orig.id, "like this", "@me", paths=tmp_paths)

    # Original marked replied
    reloaded = m.read_message(orig.path)
    assert reloaded.status == m.STATUS_REPLIED
    assert reloaded.replied_at != ""

    # Reply carries reply_to pointer and !reply label
    assert reply.reply_to == orig.id
    assert reply.label == "!reply"
    assert reply.from_ == "@me"
