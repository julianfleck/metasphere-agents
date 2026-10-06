"""Tests for metasphere.breadcrumbs (per-turn context-hook breadcrumb)."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from metasphere import breadcrumbs as _bc
from metasphere.paths import Paths


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


# ---------- count_user_messages ----------


def test_count_user_messages_empty(tmp_path: Path):
    assert _bc.count_user_messages(None) == 0
    assert _bc.count_user_messages("") == 0
    assert _bc.count_user_messages(tmp_path / "absent.jsonl") == 0


def test_count_user_messages_mixed(tmp_path: Path):
    p = tmp_path / "t.jsonl"
    _write_jsonl(
        p,
        [
            {"type": "user", "message": {"content": "u1"}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "a1"}]}},
            {"type": "user", "message": {"content": "u2"}},
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "a2"}]}},
            {"type": "user", "message": {"content": "u3"}},
        ],
    )
    assert _bc.count_user_messages(p) == 3


def test_count_user_messages_skips_tool_results(tmp_path: Path):
    """Regression: Claude Code emits tool-call results as records with
    type=='user' and message.content=[{type:'tool_result', ...}]. These
    must NOT be counted as real user prompts — otherwise the Stop-time
    count exceeds the UserPromptSubmit-time count by the number of tool
    calls in the turn and the breadcrumb fail-closed gate suppresses
    every tool-using turn (observed: 26/26 posthook fires today for
    @orchestrator with reason=count-mismatch).
    """
    p = tmp_path / "t.jsonl"
    _write_jsonl(
        p,
        [
            # 2 real user prompts (mix of legacy string-content and the
            # newer list-of-text-blocks shape).
            {"type": "user", "message": {"content": "hi"}},
            {"type": "user", "message": {"content": [{"type": "text", "text": "hi again"}]}},
            # 3 tool_result records — these are also type=='user' but
            # must be skipped.
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t1", "content": "ok"}
            ]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t2", "content": "ok"}
            ]}},
            {"type": "user", "message": {"content": [
                {"type": "tool_result", "tool_use_id": "t3", "content": "ok"}
            ]}},
            # 1 assistant record (never counted).
            {"type": "assistant", "message": {"content": [{"type": "text", "text": "hello"}]}},
        ],
    )
    assert _bc.count_user_messages(p) == 2


def test_count_user_messages_skips_compact_summary(tmp_path: Path):
    """Regression: Claude Code's auto-compact handler inserts a
    ``type=='user'`` record with ``isCompactSummary: true`` and
    ``message.content`` of the form ``"This session is being continued
    from a previous conversation…"``. The record is NOT a real user
    prompt — it does not fire UserPromptSubmit — but it is persisted to
    the JSONL transcript. Counting it inflates the Stop-time count above
    the UserPromptSubmit-time count by exactly 1 on the first turn after
    every compaction, tripping ``count-mismatch`` and silently dropping
    that turn's reply from Telegram. (Observed on @orchestrator across
    2026-05: ~5 suppressions/day, each within 10 minutes of a
    compaction.)
    """
    p = tmp_path / "t.jsonl"
    _write_jsonl(
        p,
        [
            {"type": "user", "message": {"content": "real prompt 1"}},
            # The compaction marker — Claude Code's own field.
            {
                "type": "user",
                "isCompactSummary": True,
                "message": {"content": "This session is being continued from a previous conversation..."},
            },
            {"type": "user", "message": {"content": "real prompt 2"}},
        ],
    )
    assert _bc.count_user_messages(p) == 2


def test_count_user_messages_skips_heartbeat_injections(tmp_path: Path):
    """Heartbeat injections landing during a multi-tool turn must not inflate
    the Stop-time count. Regression: 2026-05-18 count-mismatch suppression
    when a heartbeat landed mid-turn, pushing delta to 2."""
    p = tmp_path / "t.jsonl"
    heartbeat_text = "# HEARTBEAT 2026-05-18T15:51:14Z (@orchestrator)\n\nSome context..."
    p.write_text(
        "\n".join([
            # Real user message
            json.dumps({"type": "user", "message": {"content": "do the thing"}}),
            # Heartbeat as plain-string content
            json.dumps({"type": "user", "message": {"content": heartbeat_text}}),
            # Heartbeat as list-of-text-block content
            json.dumps({"type": "user", "message": {"content": [{"type": "text", "text": heartbeat_text}]}}),
            # Another real user message
            json.dumps({"type": "user", "message": {"content": "follow up"}}),
        ]) + "\n",
        encoding="utf-8",
    )
    # Only the two real user messages should count; both heartbeat forms skipped
    assert _bc.count_user_messages(p) == 2


def test_count_user_messages_skips_post_restart_wake(tmp_path: Path):
    """Post-restart wake-ups (``[session restarted] ...``) share the
    heartbeat inject shape (defer_if_busy + escape_prefix=False) and so
    can race a real user turn the same way. Filter them too so a
    restart that races a fresh user message doesn't trip the gate."""
    p = tmp_path / "t.jsonl"
    wake_text = "[session restarted] agent: @orchestrator, reason: stale-session. Check messages and tasks, resume where you left off."
    p.write_text(
        "\n".join([
            json.dumps({"type": "user", "message": {"content": "real user message"}}),
            json.dumps({"type": "user", "message": {"content": wake_text}}),
            json.dumps({"type": "user", "message": {"content": [{"type": "text", "text": wake_text}]}}),
        ]) + "\n",
        encoding="utf-8",
    )
    assert _bc.count_user_messages(p) == 1


def test_count_user_messages_skips_agent_wake_notice(tmp_path: Path):
    """Agent-to-agent wake notices (``[wake] new task from @X: ...``) are
    auto-injected by :func:`metasphere.messages.wake_recipient_if_live`
    whenever a new message lands for a live agent. Same inject shape as
    heartbeat, same race risk."""
    p = tmp_path / "t.jsonl"
    wake_text = "[wake] new task from @scheduler: cleanup:nightly-sweep — scheduled cron fire..."
    p.write_text(
        "\n".join([
            json.dumps({"type": "user", "message": {"content": "real prompt"}}),
            json.dumps({"type": "user", "message": {"content": wake_text}}),
            json.dumps({"type": "user", "message": {"content": [{"type": "text", "text": wake_text}]}}),
            json.dumps({"type": "user", "message": {"content": "another real prompt"}}),
        ]) + "\n",
        encoding="utf-8",
    )
    assert _bc.count_user_messages(p) == 2


def test_count_user_messages_keeps_task_dispatch(tmp_path: Path):
    """Scheduled-task wakes (``[task] ...``) ARE user-equivalent — they
    represent a cron/operator-triggered intent that the agent should
    process as a real turn. They MUST still be counted; only the
    auto-nudges (heartbeat / restart-wake / agent-wake) get skipped."""
    p = tmp_path / "t.jsonl"
    p.write_text(
        "\n".join([
            json.dumps({"type": "user", "message": {"content": "[task] cleanup:daily-summary — scheduled run."}}),
            json.dumps({"type": "user", "message": {"content": [{"type": "text", "text": "[task] another scheduled run"}]}}),
        ]) + "\n",
        encoding="utf-8",
    )
    assert _bc.count_user_messages(p) == 2


def test_count_user_messages_skips_task_notifications(tmp_path: Path):
    """Background/workflow ``<task-notification>`` completion notices are
    auto-injected via the same defer-if-busy tmux path as heartbeat/wake
    nudges, so they can race a real user turn and land mid-window. They
    must be skipped. NB: distinct from ``[task]`` scheduled-task wakes
    (see ``test_count_user_messages_keeps_task_dispatch``), which ARE
    counted."""
    p = tmp_path / "t.jsonl"
    notif = "<task-notification>\n<task-id>bs1f5z32n</task-id>\n<tool-use-id>toolu_x</tool-use-id>\nA background task completed."
    p.write_text(
        "\n".join([
            json.dumps({"type": "user", "message": {"content": "real prompt"}}),
            json.dumps({"type": "user", "message": {"content": notif}}),
            json.dumps({"type": "user", "message": {"content": [{"type": "text", "text": notif}]}}),
            json.dumps({"type": "user", "message": {"content": "follow up"}}),
        ]) + "\n",
        encoding="utf-8",
    )
    # Only the two real prompts count; both task-notification forms skipped.
    assert _bc.count_user_messages(p) == 2


def test_count_user_messages_skips_interrupt_sentinel(tmp_path: Path):
    """Claude Code inserts a ``[Request interrupted by ...]`` record
    whenever a turn is interrupted — e.g. an inbound Telegram message
    that Escapes the live turn instead of queuing. A single mid-turn
    interrupt adds the sentinel *plus* the real follow-up message,
    pushing the Stop-time delta to ≥2 and tripping ``count-mismatch``.
    The sentinel carries no context (the follow-up fires its own
    UserPromptSubmit), so it must be skipped; the follow-up still
    counts. Regression: residual @orchestrator suppressions during the
    2026-08 grant crunch when rapid interrupt-driven replies raced the
    breadcrumb window."""
    p = tmp_path / "t.jsonl"
    p.write_text(
        "\n".join([
            json.dumps({"type": "user", "message": {"content": "original prompt"}}),
            json.dumps({"type": "user", "message": {"content": "[Request interrupted by user]"}}),
            json.dumps({"type": "user", "message": {"content": [{"type": "text", "text": "[Request interrupted by user for tool use]"}]}}),
            json.dumps({"type": "user", "message": {"content": "the real follow-up message"}}),
        ]) + "\n",
        encoding="utf-8",
    )
    # Two real prompts count; both interrupt-sentinel forms skipped.
    assert _bc.count_user_messages(p) == 2


def test_count_user_messages_handles_garbage_lines(tmp_path: Path):
    p = tmp_path / "t.jsonl"
    p.write_text(
        "\n".join([
            json.dumps({"type": "user"}),
            "not-json",
            "",
            json.dumps({"type": "user"}),
        ]) + "\n",
        encoding="utf-8",
    )
    assert _bc.count_user_messages(p) == 2


# ---------- write/read breadcrumb ----------


def test_write_then_read_roundtrip(tmp_paths: Paths):
    ok = _bc.write_breadcrumb(
        tmp_paths,
        session_id="abc-123",
        status=_bc.STATUS_SUCCESS,
        user_msg_count=7,
        agent="@orchestrator",
    )
    assert ok is True
    bc = _bc.read_breadcrumb(tmp_paths, "abc-123")
    assert bc is not None
    assert bc["session_id"] == "abc-123"
    assert bc["user_msg_count"] == 7
    assert bc["status"] == _bc.STATUS_SUCCESS
    assert bc["agent"] == "@orchestrator"


def test_write_breadcrumb_skips_empty_session_id(tmp_paths: Paths):
    assert _bc.write_breadcrumb(tmp_paths, session_id="", status=_bc.STATUS_SUCCESS, user_msg_count=0) is False
    assert not _bc.breadcrumbs_dir(tmp_paths).exists() or not list(_bc.breadcrumbs_dir(tmp_paths).iterdir())


def test_read_breadcrumb_missing_returns_none(tmp_paths: Paths):
    assert _bc.read_breadcrumb(tmp_paths, "nope") is None


def test_breadcrumb_path_sanitizes_session_id(tmp_paths: Paths):
    # A pathological session_id with slashes must not escape the dir.
    p = _bc.breadcrumb_path(tmp_paths, "../../escape")
    assert _bc.breadcrumbs_dir(tmp_paths) in p.parents


# ---------- evaluate ----------


def test_evaluate_no_session_id(tmp_paths: Paths, tmp_path: Path):
    ok, reason = _bc.evaluate(tmp_paths, session_id="", transcript_path=None)
    assert ok is False
    assert reason == "no-session-id"


def test_evaluate_breadcrumb_missing(tmp_paths: Paths, tmp_path: Path):
    ok, reason = _bc.evaluate(tmp_paths, session_id="ghost", transcript_path=None)
    assert ok is False
    assert reason == "breadcrumb-missing"


def test_evaluate_failed_status(tmp_paths: Paths, tmp_path: Path):
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(transcript, [{"type": "user"}])
    _bc.write_breadcrumb(
        tmp_paths,
        session_id="s",
        status=_bc.STATUS_FAILED,
        user_msg_count=1,
        agent="@orchestrator",
    )
    ok, reason = _bc.evaluate(tmp_paths, session_id="s", transcript_path=transcript)
    assert ok is False
    assert reason == "context-hook-failed"


def test_evaluate_count_mismatch(tmp_paths: Paths, tmp_path: Path):
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(
        transcript,
        [{"type": "user"}, {"type": "user"}, {"type": "user"}],
    )  # count=3
    _bc.write_breadcrumb(
        tmp_paths,
        session_id="s",
        status=_bc.STATUS_SUCCESS,
        user_msg_count=1,  # stale, delta=+2 → must still fail
        agent="@orchestrator",
    )
    ok, reason = _bc.evaluate(tmp_paths, session_id="s", transcript_path=transcript)
    assert ok is False
    assert reason == "count-mismatch"


def test_evaluate_accepts_plus_one_race_delta(tmp_paths: Paths, tmp_path: Path):
    """Regression: the UserPromptSubmit hook fires before Claude Code
    flushes the current turn's user-prompt record to the JSONL
    transcript. The breadcrumb's stored count therefore lags the
    Stop-time fresh count by exactly 1 on every non-empty turn (root
    cause documented at
    ~/.metasphere/audits/2026-04-21/count-mismatch-diagnostic.md).
    evaluate() must accept fresh - stored ∈ {0, 1} as a valid match
    while still rejecting any other delta — otherwise the fail-closed
    gate suppresses every orchestrator turn.
    """
    # (a) fresh == stored: the prompt was already flushed at hook time.
    transcript_a = tmp_path / "a.jsonl"
    _write_jsonl(transcript_a, [{"type": "user"}, {"type": "user"}])
    _bc.write_breadcrumb(
        tmp_paths,
        session_id="sa",
        status=_bc.STATUS_SUCCESS,
        user_msg_count=2,
        agent="@orchestrator",
    )
    ok, reason = _bc.evaluate(tmp_paths, session_id="sa", transcript_path=transcript_a)
    assert ok is True, reason
    assert reason == "ok"

    # (b) fresh == stored + 1: the racing case — current turn's prompt
    # landed between UserPromptSubmit and Stop. Must pass.
    transcript_b = tmp_path / "b.jsonl"
    _write_jsonl(transcript_b, [{"type": "user"}, {"type": "user"}])
    _bc.write_breadcrumb(
        tmp_paths,
        session_id="sb",
        status=_bc.STATUS_SUCCESS,
        user_msg_count=1,
        agent="@orchestrator",
    )
    ok, reason = _bc.evaluate(tmp_paths, session_id="sb", transcript_path=transcript_b)
    assert ok is True, reason
    assert reason == "ok"

    # (c) fresh == stored - 1: transcript shrank since the breadcrumb
    # was written (impossible under normal Claude Code behavior — points
    # at a clobbered breadcrumb or a swapped transcript). Must fail.
    transcript_c = tmp_path / "c.jsonl"
    _write_jsonl(transcript_c, [{"type": "user"}])
    _bc.write_breadcrumb(
        tmp_paths,
        session_id="sc",
        status=_bc.STATUS_SUCCESS,
        user_msg_count=2,
        agent="@orchestrator",
    )
    ok, reason = _bc.evaluate(tmp_paths, session_id="sc", transcript_path=transcript_c)
    assert ok is False
    assert reason == "count-mismatch"

    # (d) fresh == stored + 2: a turn was added without a corresponding
    # context-hook breadcrumb refresh — the gate must still fail closed.
    transcript_d = tmp_path / "d.jsonl"
    _write_jsonl(
        transcript_d,
        [{"type": "user"}, {"type": "user"}, {"type": "user"}],
    )
    _bc.write_breadcrumb(
        tmp_paths,
        session_id="sd",
        status=_bc.STATUS_SUCCESS,
        user_msg_count=1,
        agent="@orchestrator",
    )
    ok, reason = _bc.evaluate(tmp_paths, session_id="sd", transcript_path=transcript_d)
    assert ok is False
    assert reason == "count-mismatch"


def test_evaluate_happy_path(tmp_paths: Paths, tmp_path: Path):
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(transcript, [{"type": "user"}, {"type": "user"}])
    _bc.write_breadcrumb(
        tmp_paths,
        session_id="s",
        status=_bc.STATUS_SUCCESS,
        user_msg_count=2,
        agent="@orchestrator",
    )
    ok, reason = _bc.evaluate(tmp_paths, session_id="s", transcript_path=transcript)
    assert ok is True
    assert reason == "ok"


# ---------- pruning ----------


def test_prune_removes_old_files(tmp_paths: Paths):
    # Write two breadcrumbs, then backdate one.
    _bc.write_breadcrumb(tmp_paths, session_id="fresh", status=_bc.STATUS_SUCCESS, user_msg_count=0)
    _bc.write_breadcrumb(tmp_paths, session_id="old", status=_bc.STATUS_SUCCESS, user_msg_count=0)
    old_path = _bc.breadcrumb_path(tmp_paths, "old")
    backdate = time.time() - (_bc.BREADCRUMB_MAX_AGE_SECONDS + 600)
    os.utime(old_path, (backdate, backdate))

    removed = _bc.prune_old_breadcrumbs(tmp_paths)
    assert removed == 1
    assert _bc.read_breadcrumb(tmp_paths, "fresh") is not None
    assert _bc.read_breadcrumb(tmp_paths, "old") is None


def test_prune_no_dir_is_noop(tmp_paths: Paths):
    # Don't create the dir; prune must not error.
    assert _bc.prune_old_breadcrumbs(tmp_paths) == 0
