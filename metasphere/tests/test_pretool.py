"""Tests for metasphere.cli.pretool — PreToolUse interactive-prompt guard.

Verifies the gateway interactive-prompt guard end-to-end:
- gateway env + AskUserQuestion / ExitPlanMode -> deny (correct schema)
- gateway env + a benign tool -> allow (no output)
- NO gateway env + AskUserQuestion -> allow (inert for interactive humans)
- malformed stdin / exception -> allow (default-allow, never break the host)
- redirect text branches on agent identity (orchestrator vs project agent)
"""

from __future__ import annotations

import json

from metasphere.cli import pretool
from metasphere.paths import Paths


class _FakeStdin:
    """Stand-in for sys.stdin that yields a fixed payload."""

    def __init__(self, payload: bytes) -> None:
        self.buffer = type("B", (), {"read": lambda self_: payload})()

    def isatty(self) -> bool:  # noqa: D401
        return False


def _payload(tool: str) -> bytes:
    return json.dumps({
        "hook_event_name": "PreToolUse",
        "tool_name": tool,
        "tool_input": {},
    }).encode("utf-8")


def _run(monkeypatch, stdin_bytes: bytes) -> int:
    monkeypatch.setattr("sys.stdin", _FakeStdin(stdin_bytes))
    return pretool.main([])


# --- deny paths -----------------------------------------------------------

def test_gateway_askuserquestion_denies(tmp_paths: Paths, monkeypatch, capsys):
    monkeypatch.setenv("METASPHERE_GATEWAY_SESSION", "1")
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@orchestrator")

    rc = _run(monkeypatch, _payload("AskUserQuestion"))
    assert rc == 0

    out = json.loads(capsys.readouterr().out)
    hso = out["hookSpecificOutput"]
    assert hso["hookEventName"] == "PreToolUse"
    assert hso["permissionDecision"] == "deny"
    assert "AskUserQuestion is disabled" in hso["permissionDecisionReason"]


def test_gateway_exitplanmode_denies(tmp_paths: Paths, monkeypatch, capsys):
    monkeypatch.setenv("METASPHERE_GATEWAY_SESSION", "1")
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@orchestrator")

    rc = _run(monkeypatch, _payload("ExitPlanMode"))
    assert rc == 0

    out = json.loads(capsys.readouterr().out)
    hso = out["hookSpecificOutput"]
    assert hso["permissionDecision"] == "deny"
    assert "ExitPlanMode is disabled" in hso["permissionDecisionReason"]


# --- allow paths ----------------------------------------------------------

def test_gateway_benign_tool_allows(tmp_paths: Paths, monkeypatch, capsys):
    monkeypatch.setenv("METASPHERE_GATEWAY_SESSION", "1")
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@orchestrator")

    rc = _run(monkeypatch, _payload("Bash"))
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_no_gateway_env_allows(tmp_paths: Paths, monkeypatch, capsys):
    # Interactive human: env var absent -> hook is inert even for the
    # interactive tools.
    monkeypatch.delenv("METASPHERE_GATEWAY_SESSION", raising=False)
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@orchestrator")

    rc = _run(monkeypatch, _payload("AskUserQuestion"))
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_malformed_stdin_allows(tmp_paths: Paths, monkeypatch, capsys):
    monkeypatch.setenv("METASPHERE_GATEWAY_SESSION", "1")
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@orchestrator")

    rc = _run(monkeypatch, b"this is not json {{{")
    assert rc == 0
    assert capsys.readouterr().out == ""


def test_exception_falls_through_to_allow(tmp_paths: Paths, monkeypatch, capsys):
    # A stdin object that raises on read must not break the host.
    monkeypatch.setenv("METASPHERE_GATEWAY_SESSION", "1")
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@orchestrator")

    class _Boom:
        buffer = type("B", (), {"read": lambda self_: (_ for _ in ()).throw(IOError("boom"))})()

        def isatty(self):
            return False

    monkeypatch.setattr("sys.stdin", _Boom())
    rc = pretool.main([])
    assert rc == 0
    assert capsys.readouterr().out == ""


# --- identity branching ---------------------------------------------------

def test_orchestrator_redirect_points_at_telegram(tmp_paths: Paths, monkeypatch, capsys):
    monkeypatch.setenv("METASPHERE_GATEWAY_SESSION", "1")
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@orchestrator")

    _run(monkeypatch, _payload("AskUserQuestion"))
    reason = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "metasphere telegram send" in reason
    assert "the operator" in reason
    assert "metasphere msg send" not in reason


def test_project_agent_redirect_points_at_lead(tmp_paths: Paths, monkeypatch, capsys):
    # A non-orchestrator agent with a parent file resolves to msg-send-to-lead,
    # NOT the operator's Telegram.
    agent_dir = tmp_paths.agents / "@some-eng"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "parent").write_text("@some-lead\n", encoding="utf-8")

    monkeypatch.setenv("METASPHERE_GATEWAY_SESSION", "1")
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@some-eng")

    _run(monkeypatch, _payload("AskUserQuestion"))
    reason = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "metasphere msg send @some-lead" in reason
    assert "metasphere telegram send" not in reason
    assert "Do NOT message the operator's Telegram" in reason


def test_project_agent_without_parent_defaults_to_orchestrator(tmp_paths: Paths, monkeypatch, capsys):
    # No parent file on disk -> default the lead to @orchestrator via msg send,
    # still not the operator's Telegram.
    agent_dir = tmp_paths.agents / "@lonely-eng"
    agent_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("METASPHERE_GATEWAY_SESSION", "1")
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@lonely-eng")

    _run(monkeypatch, _payload("AskUserQuestion"))
    reason = json.loads(capsys.readouterr().out)["hookSpecificOutput"]["permissionDecisionReason"]
    assert "metasphere msg send @orchestrator" in reason
    assert "metasphere telegram send" not in reason
