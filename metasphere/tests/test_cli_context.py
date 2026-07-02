"""Tests for metasphere.cli.context — UserPromptSubmit hook entry point.

Verifies the breadcrumb writer behavior end-to-end:
- success path writes a SUCCESS breadcrumb keyed by session_id with
  the correct user-message count
- a context-build exception writes a FAILED breadcrumb (best-effort)
  and still exits 0 so the host turn isn't broken
- absent stdin (manual invocation) is a no-op for breadcrumb writes
- an interactive (non-managed) session injects nothing and writes no
  breadcrumb (issue #150) — every managed agent sets the env marker
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

from metasphere import breadcrumbs as _bc
from metasphere.cli import context as cli_context
from metasphere.paths import Paths


def _write_jsonl(path: Path, records: list[dict]) -> None:
    path.write_text("\n".join(json.dumps(r) for r in records) + "\n", encoding="utf-8")


class _FakeStdin:
    """Stand-in for sys.stdin that yields a fixed payload."""

    def __init__(self, payload: bytes) -> None:
        self.buffer = type("B", (), {"read": lambda self_: payload})()

    def isatty(self) -> bool:  # noqa: D401
        return False


def _payload(transcript: Path, session_id: str) -> bytes:
    return json.dumps(
        {
            "session_id": session_id,
            "transcript_path": str(transcript),
            "hook_event_name": "UserPromptSubmit",
            "prompt": "a user prompt",
            "cwd": str(transcript.parent),
        }
    ).encode("utf-8")


def test_cli_context_writes_success_breadcrumb(tmp_paths: Paths, monkeypatch, capsys):
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@orchestrator")
    monkeypatch.setenv("METASPHERE_GATEWAY_SESSION", "1")  # managed agent session
    transcript = tmp_paths.root / "t.jsonl"
    _write_jsonl(transcript, [{"type": "user"}, {"type": "user"}])  # 2 user msgs

    payload = _payload(transcript, session_id="cli-success")
    monkeypatch.setattr("sys.stdin", _FakeStdin(payload))

    rc = cli_context.main([])
    assert rc == 0

    bc = _bc.read_breadcrumb(tmp_paths, "cli-success")
    assert bc is not None
    assert bc["status"] == _bc.STATUS_SUCCESS
    assert bc["session_id"] == "cli-success"
    assert bc["user_msg_count"] == 2
    assert bc["agent"] == "@orchestrator"

    # build_context emitted to stdout — at minimum the status header.
    out = capsys.readouterr().out
    assert "@orchestrator" in out


def test_cli_context_writes_failed_breadcrumb_on_exception(tmp_paths: Paths, monkeypatch):
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@orchestrator")
    monkeypatch.setenv("METASPHERE_GATEWAY_SESSION", "1")  # managed agent session
    transcript = tmp_paths.root / "t.jsonl"
    _write_jsonl(transcript, [{"type": "user"}])

    payload = _payload(transcript, session_id="cli-failed")
    monkeypatch.setattr("sys.stdin", _FakeStdin(payload))

    with mock.patch("metasphere.cli.context.build_context", side_effect=RuntimeError("boom")):
        rc = cli_context.main([])
    assert rc == 0  # never crash the host

    bc = _bc.read_breadcrumb(tmp_paths, "cli-failed")
    assert bc is not None
    assert bc["status"] == _bc.STATUS_FAILED
    assert bc["session_id"] == "cli-failed"
    assert bc["user_msg_count"] == 1
    assert "RuntimeError" in (bc.get("reason") or "")


def test_cli_context_threads_prompt_into_build_context(tmp_paths: Paths, monkeypatch):
    """The user's prompt is extracted from the hook payload and passed to
    build_context so memory recall can score against it (fixes the query
    that previously never saw the prompt)."""
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@orchestrator")
    monkeypatch.setenv("METASPHERE_GATEWAY_SESSION", "1")  # managed agent session
    transcript = tmp_paths.root / "t.jsonl"
    _write_jsonl(transcript, [{"type": "user"}])

    payload = _payload(transcript, session_id="cli-prompt")  # prompt="a user prompt"
    monkeypatch.setattr("sys.stdin", _FakeStdin(payload))

    with mock.patch(
        "metasphere.cli.context.build_context", return_value="## ctx\n"
    ) as bc_mock:
        rc = cli_context.main([])
    assert rc == 0
    assert bc_mock.call_args.kwargs.get("prompt") == "a user prompt"


def test_cli_context_interactive_session_skips_everything(tmp_paths: Paths, monkeypatch, capsys):
    """An interactive Claude Code session a human opened by hand (no
    METASPHERE_GATEWAY_SESSION marker) must NOT get the orchestrator
    persona injected — that spins up a second orchestrator that fights
    the gateway poller for Telegram getUpdates (issue #150). The hook
    emits nothing, writes no breadcrumb, and never calls build_context.
    """
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@orchestrator")
    monkeypatch.delenv("METASPHERE_GATEWAY_SESSION", raising=False)  # interactive
    transcript = tmp_paths.root / "t.jsonl"
    _write_jsonl(transcript, [{"type": "user"}])

    payload = _payload(transcript, session_id="cli-interactive")
    monkeypatch.setattr("sys.stdin", _FakeStdin(payload))

    with mock.patch("metasphere.cli.context.build_context") as bc_mock:
        rc = cli_context.main([])
    assert rc == 0
    bc_mock.assert_not_called()  # no persona/context injection

    # Nothing emitted to stdout, and no breadcrumb (no managed bookkeeping).
    assert capsys.readouterr().out == ""
    assert _bc.read_breadcrumb(tmp_paths, "cli-interactive") is None


def test_cli_context_managed_marker_enables_injection(tmp_paths: Paths, monkeypatch):
    """With the managed marker set, the same payload that the interactive
    test skips now flows through build_context and writes a breadcrumb —
    proving the gate keys on the marker, not the payload."""
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@orchestrator")
    monkeypatch.setenv("METASPHERE_GATEWAY_SESSION", "1")
    transcript = tmp_paths.root / "t.jsonl"
    _write_jsonl(transcript, [{"type": "user"}])

    payload = _payload(transcript, session_id="cli-managed")
    monkeypatch.setattr("sys.stdin", _FakeStdin(payload))

    with mock.patch(
        "metasphere.cli.context.build_context", return_value="## ctx\n"
    ) as bc_mock:
        rc = cli_context.main([])
    assert rc == 0
    bc_mock.assert_called_once()
    assert _bc.read_breadcrumb(tmp_paths, "cli-managed") is not None


def test_cli_context_no_stdin_skips_breadcrumb(tmp_paths: Paths, monkeypatch):
    """Manual invocation from a shell (no JSON on stdin) is allowed —
    we just don't write a breadcrumb, and the posthook will fail-closed
    for that session, which is the correct default.
    """
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@orchestrator")
    monkeypatch.setenv("METASPHERE_GATEWAY_SESSION", "1")  # managed agent session
    monkeypatch.setattr("sys.stdin", _FakeStdin(b""))
    rc = cli_context.main([])
    assert rc == 0
    # No breadcrumbs dir created (or empty if it was).
    bdir = _bc.breadcrumbs_dir(tmp_paths)
    assert not bdir.exists() or not list(bdir.iterdir())
