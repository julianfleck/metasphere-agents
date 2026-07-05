"""Tests for the outbox-orphan sweep (silent-loss backstop).

An outbox ``.msg`` with no inbox/archive twin is a message the bus
never carried — nothing woke the recipient, no inbox view shows it,
``msg done`` can't resolve it. Real incident 2026-07-05 20:52: an
agent hand-wrote its ``!done`` straight into its project outbox
(believing outbox = send queue); the sign-off sat invisible ~1h.
``sweep_outbox_orphans`` late-delivers such orphans through the same
primitives ``send_message`` uses, preserving the original id so
``reply_to`` threading stays intact.
"""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from unittest.mock import patch

from metasphere import messages as m


def _iso_ago(seconds: int) -> str:
    ts = dt.datetime.now(dt.timezone.utc) - dt.timedelta(seconds=seconds)
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_orphan(
    tmp_paths,
    *,
    msg_id: str = "msg-1783284750-189719841",
    to: str = "@orchestrator",
    label: str = "!done",
    age_seconds: int = 600,
    project: str = "testproj",
) -> Path:
    """Hand-write an outbox-only message file, exactly mirroring the
    incident shape (fabricated id, no inbox copy, no event)."""
    outbox = tmp_paths.projects / project / ".messages" / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    f = outbox / f"{msg_id}.msg"
    f.write_text(
        "---\n"
        f"id: {msg_id}\n"
        'from: "@rage-annotator"\n'
        f'to: "{to}"\n'
        f'label: "{label}"\n'
        "status: unread\n"
        "scope: /\n"
        f"created: {_iso_ago(age_seconds)}\n"
        "read_at: \n"
        "replied_at: \n"
        "completed_at: \n"
        "reply_to: msg-1783284648-3317242\n"
        "last_pinged_at: \n"
        "ping_count: 0\n"
        "---\n"
        "SIGN-OFF: ship the state-both render.\n",
        encoding="utf-8",
    )
    return f


def _testproj_inbox(tmp_paths) -> Path:
    return tmp_paths.projects / "testproj" / ".messages" / "inbox"


def test_orphan_is_late_delivered_with_same_id(tmp_paths):
    """The core backstop: outbox-only orphan inside the window gets an
    inbox copy under its ORIGINAL id (threading intact), is indexed,
    and the recipient wake is attempted."""
    _write_orphan(tmp_paths, age_seconds=600)

    wakes: list[tuple] = []
    with patch(
        "metasphere.messages.wake_recipient_if_live",
        side_effect=lambda *a, **k: wakes.append(a) or True,
    ):
        results = m.sweep_outbox_orphans(tmp_paths)

    assert len(results) == 1, f"expected one delivery, got {results}"
    r = results[0]
    assert r["action"] == "orphan-delivered"
    assert r["msg_id"] == "msg-1783284750-189719841"
    assert r["label"] == "!done"

    inbox_copy = _testproj_inbox(tmp_paths) / "msg-1783284750-189719841.msg"
    assert inbox_copy.is_file(), "inbox twin must exist after the sweep"
    delivered = m.read_message(inbox_copy)
    assert delivered.id == "msg-1783284750-189719841"
    assert delivered.reply_to == "msg-1783284648-3317242", (
        "original id/threading fields must survive late delivery"
    )
    assert "SIGN-OFF" in delivered.body

    assert len(wakes) == 1, "recipient wake must be attempted"
    assert wakes[0][0] == "@orchestrator"


def test_sweep_is_idempotent(tmp_paths):
    """A delivered orphan has an inbox twin — the next sweep must not
    touch it again (no duplicate delivery, no duplicate wake)."""
    _write_orphan(tmp_paths, age_seconds=600)
    with patch("metasphere.messages.wake_recipient_if_live", return_value=True):
        first = m.sweep_outbox_orphans(tmp_paths)
        second = m.sweep_outbox_orphans(tmp_paths)
    assert len(first) == 1
    assert second == [], f"second sweep must be a no-op, got {second}"


def test_fresh_orphan_below_min_age_is_skipped(tmp_paths):
    """A just-written outbox file may be a normal in-flight send whose
    inbox copy is milliseconds away — never race it."""
    _write_orphan(tmp_paths, age_seconds=10)
    results = m.sweep_outbox_orphans(tmp_paths)
    assert results == []
    assert not (_testproj_inbox(tmp_paths) / "msg-1783284750-189719841.msg").exists()


def test_ancient_orphan_beyond_max_age_is_skipped(tmp_paths):
    """Historical backlog (and any future retention-purged inbox copy)
    stays untouched — the max-age bound caps first-sweep blast radius."""
    _write_orphan(tmp_paths, age_seconds=200_000)  # > 86400 default
    results = m.sweep_outbox_orphans(tmp_paths)
    assert results == []
    assert not (_testproj_inbox(tmp_paths) / "msg-1783284750-189719841.msg").exists()


def test_normal_sent_message_is_not_touched(tmp_paths):
    """A message sent through send_message has an inbox twin from
    birth — the sweep must classify it as delivered even though its
    outbox copy exists."""
    msg = m.send_message(
        "@orchestrator", "!info", "regular send", "@child",
        paths=tmp_paths, wake=False,
    )
    # Age the outbox copy past min_age by rewriting created.
    out_copy = (tmp_paths.projects / "testproj" / ".messages" / "outbox"
                / f"{msg.id}.msg")
    assert out_copy.exists()
    results = m.sweep_outbox_orphans(tmp_paths, min_age_seconds=0)
    assert results == [], f"delivered message must not re-deliver: {results}"


def test_archived_copy_counts_as_delivered(tmp_paths):
    """An inbox copy that has moved to a day-dir archive is still a
    delivered message — no re-delivery from the sender-side copy."""
    f = _write_orphan(tmp_paths, age_seconds=600)
    archive_day = (tmp_paths.projects / "testproj" / ".messages"
                   / "archive" / "2026-07-05")
    archive_day.mkdir(parents=True)
    (archive_day / f.name).write_text(f.read_text(encoding="utf-8"),
                                      encoding="utf-8")
    results = m.sweep_outbox_orphans(tmp_paths)
    assert results == []
    assert not (_testproj_inbox(tmp_paths) / f.name).exists()


def test_dry_run_reports_without_writing(tmp_paths):
    _write_orphan(tmp_paths, age_seconds=600)
    with patch(
        "metasphere.messages.wake_recipient_if_live",
        side_effect=AssertionError("dry_run must not wake"),
    ):
        results = m.sweep_outbox_orphans(tmp_paths, dry_run=True)
    assert len(results) == 1
    assert results[0]["action"] == "would-orphan-deliver"
    assert not (_testproj_inbox(tmp_paths) / "msg-1783284750-189719841.msg").exists()


def test_malformed_file_does_not_abort_sweep(tmp_paths):
    """One unparseable .msg must not starve delivery of the others —
    the sweep runs on the consolidate tick and must never raise."""
    outbox = tmp_paths.projects / "testproj" / ".messages" / "outbox"
    outbox.mkdir(parents=True, exist_ok=True)
    (outbox / "msg-000-garbage.msg").write_text(
        "not frontmatter at all", encoding="utf-8",
    )
    _write_orphan(tmp_paths, msg_id="msg-1783285716-4410221", age_seconds=600)
    with patch("metasphere.messages.wake_recipient_if_live", return_value=True):
        results = m.sweep_outbox_orphans(tmp_paths)
    assert [r["msg_id"] for r in results] == ["msg-1783285716-4410221"]


def test_wake_failure_does_not_undo_delivery(tmp_paths):
    """The inbox copy is the load-bearing effect; a wake failure only
    delays visibility until the recipient's next inbox view."""
    _write_orphan(tmp_paths, age_seconds=600)
    with patch(
        "metasphere.messages.wake_recipient_if_live",
        side_effect=RuntimeError("tmux gone"),
    ):
        results = m.sweep_outbox_orphans(tmp_paths)
    assert len(results) == 1
    assert (_testproj_inbox(tmp_paths) / "msg-1783284750-189719841.msg").is_file()


def test_max_deliveries_caps_one_sweep(tmp_paths):
    for i in range(4):
        _write_orphan(
            tmp_paths, msg_id=f"msg-178328600{i}-99{i}", age_seconds=600,
        )
    with patch("metasphere.messages.wake_recipient_if_live", return_value=True):
        results = m.sweep_outbox_orphans(tmp_paths, max_deliveries=2)
    assert len(results) == 2
    # Remainder lands on the next tick.
    with patch("metasphere.messages.wake_recipient_if_live", return_value=True):
        rest = m.sweep_outbox_orphans(tmp_paths, max_deliveries=25)
    assert len(rest) == 2


def test_unroutable_recipient_is_left_alone(tmp_paths):
    """A fabricated file with a garbage ``to`` has no inbox to land in
    — skip it rather than guessing."""
    _write_orphan(tmp_paths, to="", age_seconds=600)
    results = m.sweep_outbox_orphans(tmp_paths)
    assert results == []


def test_run_pass_wires_orphan_sweep(tmp_paths):
    """consolidate.run_pass surfaces sweep results on the report."""
    from metasphere import consolidate as c

    _write_orphan(tmp_paths, age_seconds=600)
    with patch("metasphere.messages.wake_recipient_if_live", return_value=True):
        report = c.run_pass(
            project_root=tmp_paths.project_root, paths=tmp_paths, dry_run=False,
        )
    assert [r["msg_id"] for r in report.orphan_results] == [
        "msg-1783284750-189719841"
    ]
