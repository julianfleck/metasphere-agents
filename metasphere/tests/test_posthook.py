"""Tests for metasphere.posthook (Stop-hook port)."""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest

from metasphere import posthook
from metasphere.paths import Paths


# ---------- read_stop_hook_payload ----------

def test_read_stop_hook_payload_parses_json():
    payload = {
        "session_id": "abc",
        "transcript_path": "/tmp/x.jsonl",
        "hook_event_name": "Stop",
        "stop_hook_active": False,
    }
    out = posthook.read_stop_hook_payload(json.dumps(payload).encode("utf-8"))
    assert out == payload


def test_read_stop_hook_payload_empty_returns_empty_dict():
    assert posthook.read_stop_hook_payload(b"") == {}
    assert posthook.read_stop_hook_payload(b"not json") == {}


# ---------- extract_last_assistant_text ----------

def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


def test_extract_last_assistant_text_multi_block(tmp_path: Path):
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(
        transcript,
        [
            {"type": "user", "message": {"content": "hi"}},
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "older"},
                    ]
                },
            },
            {"type": "user", "message": {"content": "again"}},
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "block one"},
                        {"type": "tool_use", "name": "x"},
                        {"type": "text", "text": "block two"},
                    ]
                },
            },
        ],
    )
    text = posthook.extract_last_assistant_text(transcript)
    assert text == "block one\nblock two"


def test_extract_last_assistant_text_empty_transcript(tmp_path: Path):
    transcript = tmp_path / "empty.jsonl"
    transcript.write_text("", encoding="utf-8")
    assert posthook.extract_last_assistant_text(transcript) is None


def test_extract_last_assistant_text_missing_file(tmp_path: Path):
    assert posthook.extract_last_assistant_text(tmp_path / "nope.jsonl") is None


def test_extract_last_assistant_text_only_tool_use(tmp_path: Path):
    transcript = tmp_path / "t.jsonl"
    _write_jsonl(
        transcript,
        [
            {
                "type": "assistant",
                "message": {"content": [{"type": "tool_use", "name": "Bash"}]},
            }
        ],
    )
    assert posthook.extract_last_assistant_text(transcript) is None


# ---------- should_skip_silent_tick ----------

def test_should_skip_silent_tick():
    assert posthook.should_skip_silent_tick("") is True
    assert posthook.should_skip_silent_tick("   \n\t") is True
    assert posthook.should_skip_silent_tick(None) is True
    assert posthook.should_skip_silent_tick("real reply text") is False


# ---------- route_to_telegram ----------

def _write_chat_id(paths: Paths) -> None:
    paths.config.mkdir(parents=True, exist_ok=True)
    (paths.config / "telegram_chat_id").write_text("12345", encoding="utf-8")


def test_route_to_telegram_sends_once_and_dedupes(tmp_paths: Paths):
    _write_chat_id(tmp_paths)
    with mock.patch("metasphere.telegram.api.send_message") as m:
        m.return_value = [{"ok": True}]
        posthook.route_to_telegram("hello world", tmp_paths)
        posthook.route_to_telegram("hello world", tmp_paths)  # duplicate
    assert m.call_count == 1
    args, kwargs = m.call_args
    assert args[0] == "12345"
    assert args[1] == "hello world"
    # Hash file persisted
    assert (tmp_paths.state / "posthook_last_sent").exists()


def test_route_to_telegram_distinct_messages_both_sent(tmp_paths: Paths):
    _write_chat_id(tmp_paths)
    with mock.patch("metasphere.telegram.api.send_message") as m:
        m.return_value = [{"ok": True}]
        posthook.route_to_telegram("first", tmp_paths)
        posthook.route_to_telegram("second", tmp_paths)
    assert m.call_count == 2


def test_route_to_telegram_logs_on_api_failure(tmp_paths: Paths):
    _write_chat_id(tmp_paths)
    with mock.patch("metasphere.telegram.api.send_message") as m:
        m.side_effect = RuntimeError("boom")
        posthook.route_to_telegram("payload", tmp_paths)
    log = tmp_paths.state / "posthook_telegram_errors.log"
    assert log.exists()
    body = log.read_text(encoding="utf-8")
    assert "boom" in body
    assert "RuntimeError" in body


def test_route_to_telegram_logs_when_chat_id_missing(tmp_paths: Paths):
    with mock.patch("metasphere.telegram.api.send_message") as m:
        posthook.route_to_telegram("hi", tmp_paths)
    m.assert_not_called()
    log = tmp_paths.state / "posthook_telegram_errors.log"
    assert log.exists()
    assert "chat_id" in log.read_text(encoding="utf-8")


# ---------- track_turn_completion ----------

def test_track_turn_completion_increments(tmp_paths: Paths):
    posthook.track_turn_completion("@orchestrator", tmp_paths)
    posthook.track_turn_completion("@orchestrator", tmp_paths)
    posthook.track_turn_completion("@orchestrator", tmp_paths)
    activity = tmp_paths.agent_dir("@orchestrator") / "activity.json"
    assert activity.exists()
    data = json.loads(activity.read_text(encoding="utf-8"))
    assert data["turns"] == 3
    assert "updated_at" in data


def test_track_turn_completion_upgrades_spawned_status(tmp_paths: Paths):
    agent_dir = tmp_paths.agent_dir("@child")
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "status").write_text("spawned", encoding="utf-8")
    posthook.track_turn_completion("@child", tmp_paths)
    assert (agent_dir / "status").read_text(encoding="utf-8").strip() == "active"
    assert (agent_dir / "updated_at").exists()


def test_track_turn_completion_logs_heartbeat_every_10(tmp_paths: Paths):
    with mock.patch("metasphere.posthook.log_event") as m:
        for _ in range(11):
            posthook.track_turn_completion("@orchestrator", tmp_paths)
    # Called exactly once at turn 10.
    assert m.call_count == 1
    args, kwargs = m.call_args
    assert args[0] == "agent.heartbeat"
    assert "turn 10" in args[1]


# ---------- run_posthook (top-level) ----------

def test_run_posthook_never_raises_on_garbage(tmp_paths: Paths):
    assert posthook.run_posthook(b"garbage", tmp_paths) == 0


def test_run_posthook_routes_orchestrator(tmp_paths: Paths, monkeypatch):
    _write_chat_id(tmp_paths)
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@orchestrator")
    transcript = tmp_paths.root / "t.jsonl"
    _write_jsonl(
        transcript,
        [
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "the reply"}]},
            }
        ],
    )
    payload = json.dumps(
        {
            "session_id": "s",
            "transcript_path": str(transcript),
            "hook_event_name": "Stop",
            "stop_hook_active": False,
        }
    ).encode("utf-8")
    with mock.patch("metasphere.telegram.api.send_message") as m:
        m.return_value = [{"ok": True}]
        rc = posthook.run_posthook(payload, tmp_paths)
    assert rc == 0
    m.assert_called_once()
    # Activity tracked
    assert (tmp_paths.agent_dir("@orchestrator") / "activity.json").exists()


def test_run_posthook_skips_for_subagent(tmp_paths: Paths, monkeypatch):
    _write_chat_id(tmp_paths)
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@child")
    transcript = tmp_paths.root / "t.jsonl"
    _write_jsonl(
        transcript,
        [
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "reply"}]},
            }
        ],
    )
    payload = json.dumps(
        {"transcript_path": str(transcript), "stop_hook_active": False}
    ).encode("utf-8")
    with mock.patch("metasphere.telegram.api.send_message") as m:
        posthook.run_posthook(payload, tmp_paths)
    m.assert_not_called()
    # But activity still tracked for the child
    assert (tmp_paths.agent_dir("@child") / "activity.json").exists()


def test_run_posthook_respects_stop_hook_active(tmp_paths: Paths, monkeypatch):
    _write_chat_id(tmp_paths)
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@orchestrator")
    transcript = tmp_paths.root / "t.jsonl"
    _write_jsonl(
        transcript,
        [
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "reply"}]},
            }
        ],
    )
    payload = json.dumps(
        {"transcript_path": str(transcript), "stop_hook_active": True}
    ).encode("utf-8")
    with mock.patch("metasphere.telegram.api.send_message") as m:
        posthook.run_posthook(payload, tmp_paths)
    m.assert_not_called()


# ---------- auto_close_finished_task ----------

def _make_task(repo: Path, slug_title: str) -> str:
    from metasphere import tasks as _tasks
    t = _tasks.create_task(slug_title, "!normal", repo, repo, created_by="@parent")
    return t.id


def test_auto_close_finished_task_archives_on_complete_status(tmp_paths: Paths):
    task_id = _make_task(tmp_paths.project_root, "child trivial task")
    agent_dir = tmp_paths.agent_dir("@child")
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "task_id").write_text(task_id + "\n")
    (agent_dir / "status").write_text("complete: did the thing\n")

    closed = posthook.auto_close_finished_task("@child", tmp_paths)
    assert closed == task_id

    from metasphere import tasks as _tasks
    p = _tasks._find_task_file(task_id, include_completed=False)
    assert p is None  # not in active anymore
    p2 = _tasks._find_task_file(task_id, include_completed=True)
    assert p2 is not None and "archive" in str(p2)


def test_auto_close_skips_when_status_not_complete(tmp_paths: Paths):
    task_id = _make_task(tmp_paths.project_root, "still working")
    agent_dir = tmp_paths.agent_dir("@child")
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "task_id").write_text(task_id + "\n")
    (agent_dir / "status").write_text("working: halfway there\n")

    assert posthook.auto_close_finished_task("@child", tmp_paths) is None

    from metasphere import tasks as _tasks
    p = _tasks._find_task_file(task_id, include_completed=False)
    assert p is not None  # still active


def test_auto_close_no_task_id_is_noop(tmp_paths: Paths):
    agent_dir = tmp_paths.agent_dir("@legacy")
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "status").write_text("complete: done\n")
    assert posthook.auto_close_finished_task("@legacy", tmp_paths) is None


def test_auto_close_already_archived_is_noop(tmp_paths: Paths):
    task_id = _make_task(tmp_paths.project_root, "double close")
    agent_dir = tmp_paths.agent_dir("@child")
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "task_id").write_text(task_id + "\n")
    (agent_dir / "status").write_text("complete: done\n")
    # First call closes it
    assert posthook.auto_close_finished_task("@child", tmp_paths) == task_id
    # Second call is a no-op (no active file)
    assert posthook.auto_close_finished_task("@child", tmp_paths) is None


def test_run_posthook_auto_closes_for_subagent(tmp_paths: Paths, monkeypatch):
    task_id = _make_task(tmp_paths.project_root, "subagent end-to-end")
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@worker")
    agent_dir = tmp_paths.agent_dir("@worker")
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "task_id").write_text(task_id + "\n")
    (agent_dir / "status").write_text("complete: shipped\n")

    payload = json.dumps({"stop_hook_active": False}).encode("utf-8")
    rc = posthook.run_posthook(payload, tmp_paths)
    assert rc == 0

    from metasphere import tasks as _tasks
    assert _tasks._find_task_file(task_id, include_completed=False) is None


def test_run_posthook_does_not_auto_close_orchestrator(tmp_paths: Paths, monkeypatch):
    _write_chat_id(tmp_paths)
    task_id = _make_task(tmp_paths.project_root, "orchestrator task")
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@orchestrator")
    agent_dir = tmp_paths.agent_dir("@orchestrator")
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "task_id").write_text(task_id + "\n")
    (agent_dir / "status").write_text("complete: done\n")

    payload = json.dumps({"stop_hook_active": False}).encode("utf-8")
    posthook.run_posthook(payload, tmp_paths)

    from metasphere import tasks as _tasks
    # Orchestrator never auto-closes — it's persistent, not ephemeral.
    assert _tasks._find_task_file(task_id, include_completed=False) is not None


# ---------- cli --dry-run / --help ----------

def test_cli_posthook_help():
    from metasphere.cli import posthook as cli_posthook
    rc = cli_posthook.main(["--help"])
    assert rc == 0


def test_cli_posthook_dry_run_prints_json(tmp_paths: Paths, monkeypatch, capsys):
    _write_chat_id(tmp_paths)
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@orchestrator")
    transcript = tmp_paths.root / "t.jsonl"
    _write_jsonl(
        transcript,
        [
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "the reply body"}]},
            }
        ],
    )
    payload = json.dumps(
        {"transcript_path": str(transcript), "stop_hook_active": False}
    ).encode("utf-8")
    monkeypatch.setattr("sys.stdin", type("S", (), {"isatty": lambda self: False, "buffer": type("B", (), {"read": lambda self: payload})()})())
    from metasphere.cli import posthook as cli_posthook
    with mock.patch("metasphere.telegram.api.send_message") as m:
        rc = cli_posthook.main(["--dry-run"])
    assert rc == 0
    m.assert_not_called()
    out = capsys.readouterr().out.strip()
    summary = json.loads(out)
    assert summary["text_length"] == len("the reply body")
    assert summary["chunk_count"] == 1
    assert summary["chat_id"] == "12345"
    assert summary["would_send"] is True
    # Dry-run must not have written dedupe state.
    assert not (tmp_paths.state / "posthook_last_sent").exists()
