"""Tests for metasphere.messages.

Covers ordered frontmatter round-trips, field updates under flock, and
parent scope walking.
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

    # Outbox copy exists at canonical location (~/.metasphere/projects/
    # testproj/.messages/outbox/). The tmp_paths fixture registers the
    # repo as 'testproj' so Project.for_cwd(scope) resolves to it.
    outbox = (tmp_paths.projects / "testproj" / ".messages" / "outbox"
              / f"{msg.id}.msg")
    assert outbox.exists(), f"outbox copy not at {outbox}"


# ---------------------------------------------------------------------------
# Inbox walking
# ---------------------------------------------------------------------------


def test_collect_inbox_returns_project_and_global(tmp_paths):
    """Canonical layout (PR #10): one ``.messages/inbox/`` per project
    plus the global bucket. ``collect_inbox`` returns
    project-for-scope + global; the old per-subdir nested walk doesn't
    apply because subdirectories no longer carry their own inboxes.
    """
    testproj_inbox = (tmp_paths.projects / "testproj" / ".messages" / "inbox")
    global_inbox = tmp_paths.root / "messages" / "inbox"
    testproj_inbox.mkdir(parents=True, exist_ok=True)
    global_inbox.mkdir(parents=True, exist_ok=True)

    for inbox, label in [(testproj_inbox, "!proj"), (global_inbox, "!global")]:
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

    # Collecting from the project scope → sees project + global.
    msgs = m.collect_inbox(tmp_paths.project_root, tmp_paths.project_root)
    assert {x.label for x in msgs} == {"!proj", "!global"}


def test_send_to_absolute_path_target_routes_to_global(tmp_paths, tmp_path):
    """Canonical layout (PR #10): ``@/abs/path/`` still resolves the
    target scope to the absolute filesystem path (the doubled-prefix
    bug is still prevented) but the MESSAGE itself lands in the
    canonical per-project / global bucket, not at the scope dir.

    Since the abs path isn't a registered project, the message goes
    to ``~/.metasphere/messages/inbox/`` (the global sentinel).
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
    # Canonical global inbox, not the abs_target itself.
    global_inbox = tmp_paths.root / "messages" / "inbox" / f"{msg.id}.msg"
    assert global_inbox.exists(), f"message not in global inbox: {global_inbox}"
    # Not in the abs_target scope.
    assert not (abs_target / ".messages" / "inbox" / f"{msg.id}.msg").exists()
    # Not in the old doubled-prefix location either.
    doubled = tmp_paths.project_root / str(abs_target).lstrip("/")
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


# ---------------------------------------------------------------------------
# @-mention parsing
# ---------------------------------------------------------------------------


def _seed_project(tmp_paths, name: str) -> None:
    from metasphere.io import write_json
    pf = tmp_paths.root / "projects.json"
    data = []
    if pf.exists():
        import json
        data = json.loads(pf.read_text())
    data.append({"name": name, "path": "/tmp/" + name, "registered": "2026-04-08T00:00:00Z"})
    write_json(pf, data)


def _seed_agent(tmp_paths, name: str) -> None:
    (tmp_paths.root / "agents" / f"@{name}").mkdir(parents=True, exist_ok=True)


def test_extract_mentions_project_only(tmp_paths):
    _seed_project(tmp_paths, "recurse")
    ms = m.extract_mentions("hey @recurse take a look", paths=tmp_paths)
    assert len(ms) == 1
    assert ms[0].name == "recurse"
    assert ms[0].type == "project"
    assert ms[0].raw == "@recurse"


def test_extract_mentions_agent_only(tmp_paths):
    _seed_agent(tmp_paths, "julian")
    ms = m.extract_mentions("ping @julian please", paths=tmp_paths)
    assert [(x.name, x.type) for x in ms] == [("julian", "agent")]


def test_extract_mentions_collision_project_wins(tmp_paths):
    _seed_project(tmp_paths, "recurse")
    _seed_agent(tmp_paths, "recurse")
    ms = m.extract_mentions("@recurse hi", paths=tmp_paths)
    assert len(ms) == 1
    assert ms[0].type == "project"


def test_extract_mentions_unknown(tmp_paths):
    ms = m.extract_mentions("@nobody around?", paths=tmp_paths)
    assert len(ms) == 1
    assert ms[0].type == "unknown"
    assert ms[0].name == "nobody"


def test_view_marks_info_read_and_stamps_read_at(tmp_paths):
    msg = m.send_message(
        "@..", "!info", "fyi", "@child", paths=tmp_paths, wake=False
    )
    assert msg.status == m.STATUS_UNREAD
    loaded = m.read_message(msg.path, view=True)
    assert loaded.status == m.STATUS_READ
    assert loaded.read_at != ""
    # Persisted
    reloaded = m.read_message(msg.path)
    assert reloaded.status == m.STATUS_READ
    assert reloaded.read_at == loaded.read_at


def test_view_does_not_mark_task_messages_read(tmp_paths):
    msg = m.send_message(
        "@..", "!task", "do the thing", "@child", paths=tmp_paths, wake=False
    )
    loaded = m.read_message(msg.path, view=True)
    assert loaded.status == m.STATUS_UNREAD
    assert loaded.read_at == ""


def test_view_does_not_mark_query_messages_read(tmp_paths):
    msg = m.send_message(
        "@..", "!query", "ping?", "@child", paths=tmp_paths, wake=False
    )
    loaded = m.read_message(msg.path, view=True)
    assert loaded.status == m.STATUS_UNREAD


def test_view_no_op_without_flag(tmp_paths):
    msg = m.send_message(
        "@..", "!info", "fyi", "@child", paths=tmp_paths, wake=False
    )
    loaded = m.read_message(msg.path)
    assert loaded.status == m.STATUS_UNREAD
    assert loaded.read_at == ""


def test_collect_inbox_view_marks_nonsacred_read(tmp_paths):
    m.send_message("@.", "!info", "a", "@c", paths=tmp_paths, wake=False)
    m.send_message("@.", "!task", "b", "@c", paths=tmp_paths, wake=False)
    m.send_message("@.", "!done", "c", "@c", paths=tmp_paths, wake=False)
    msgs = m.collect_inbox(tmp_paths.scope, tmp_paths.project_root, view=True)
    by_label = {mm.label: mm for mm in msgs}
    assert by_label["!info"].status == m.STATUS_READ
    assert by_label["!done"].status == m.STATUS_READ
    assert by_label["!task"].status == m.STATUS_UNREAD


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
