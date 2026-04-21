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
    /home/openclaw/.metasphere/audits/2026-04-21/count-mismatch-diagnostic.md).
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
