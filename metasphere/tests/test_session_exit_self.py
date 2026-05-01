"""Tests for ``metasphere session exit-self``.

Phase H final: exit-self synchronously sends ``/exit`` to the caller's
own tmux pane (resolved from $METASPHERE_AGENT_ID), replacing the
deferred-command marker path. The marker path could never fire on
empty REPL panes (cron-fired single-shot sessions emit no Stop hook),
so cron-fired ephemerals zombied. Synchronous send removes the
dependency on Stop-hook ticks.
"""

from __future__ import annotations

from unittest.mock import patch

from metasphere.cli import session as cli_session


def _agent_record(name: str, project: str = ""):
    """Minimal AgentRecord stand-in for resolver tests."""
    from metasphere.agents import AgentRecord

    return AgentRecord(
        name=name,
        scope="",
        parent="",
        status="",
        spawned_at="",
        project=project,
    )


def test_exit_self_sends_exit_to_resolved_session(monkeypatch):
    """Happy path: agent set, session alive → tmux receives ``/exit``."""
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@worker-cron-1")

    calls: list[tuple] = []

    def _record(*args):
        calls.append(args)

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        return R()

    with patch(
        "metasphere.cli.session._resolve_session",
        return_value="metasphere-worker-cron-1",
    ), patch(
        "metasphere.cli.session.session_alive", return_value=True
    ), patch(
        "metasphere.cli.session._tmux", side_effect=_record
    ), patch(
        "metasphere.cli.session.time.sleep", return_value=None
    ):
        rc = cli_session.main(["exit-self"])

    assert rc == 0
    # The send-keys sequence mirrors restart_agent_session: C-c, C-c,
    # C-u, /exit literal, Enter, Enter.
    sent_args = [c for c in calls if c and c[0] == "send-keys"]
    assert len(sent_args) == 6
    # Final call should be send-keys -t <session> Enter (belt-and-suspenders).
    assert sent_args[-1] == ("send-keys", "-t", "metasphere-worker-cron-1", "Enter")
    # The /exit literal must use ``-l --`` so flags inside the payload
    # are not parsed by tmux.
    exit_calls = [c for c in sent_args if "/exit" in c]
    assert exit_calls, "expected one send-keys carrying '/exit' literal"
    assert "-l" in exit_calls[0]


def test_exit_self_no_agent_env_returns_1(monkeypatch, capsys):
    """No $METASPHERE_AGENT_ID → exit code 1 + stderr message, no tmux send."""
    monkeypatch.delenv("METASPHERE_AGENT_ID", raising=False)

    with patch("metasphere.cli.session._tmux", side_effect=AssertionError("tmux must not be called")):
        rc = cli_session.main(["exit-self"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "METASPHERE_AGENT_ID" in err


def test_exit_self_headless_no_tmux_returns_1(monkeypatch, capsys):
    """Agent has no live tmux session (headless ``claude -p``) →
    exit code 1, clean stderr, no crash."""
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@headless-spawn")

    with patch(
        "metasphere.cli.session._resolve_session",
        return_value="metasphere-headless-spawn",
    ), patch(
        "metasphere.cli.session.session_alive", return_value=False
    ), patch(
        "metasphere.cli.session._tmux", side_effect=AssertionError("tmux must not be called")
    ):
        rc = cli_session.main(["exit-self"])

    assert rc == 1
    err = capsys.readouterr().err
    assert "@headless-spawn" in err
    assert "metasphere-headless-spawn" in err


def test_exit_self_emits_agent_exit_self_event(monkeypatch):
    """Successful exit-self appends an ``agent.exit_self`` record so the
    silent-success path is observable in the events log. Without this
    emit, a cron-fired session that exits cleanly leaves no trace
    between ``agent.session`` (start) and the next reap sweep.
    """
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@worker-cron-1")

    recorded: list[dict] = []

    def _fake_log_event(type_, message, *, agent=None, meta=None, **_kw):
        recorded.append(
            {"type": type_, "message": message, "agent": agent, "meta": meta or {}}
        )

    with patch(
        "metasphere.cli.session._resolve_session",
        return_value="metasphere-worker-cron-1",
    ), patch(
        "metasphere.cli.session.session_alive", return_value=True
    ), patch(
        "metasphere.cli.session._tmux",
        return_value=type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
    ), patch(
        "metasphere.cli.session.time.sleep", return_value=None
    ), patch(
        "metasphere.cli.session.log_event", side_effect=_fake_log_event
    ):
        rc = cli_session.main(["exit-self"])

    assert rc == 0
    exit_evts = [r for r in recorded if r["type"] == "agent.exit_self"]
    assert len(exit_evts) == 1, f"expected one agent.exit_self event, got {recorded}"
    evt = exit_evts[0]
    assert evt["agent"] == "@worker-cron-1"
    assert evt["meta"].get("session") == "metasphere-worker-cron-1"


def test_exit_self_event_emit_failure_does_not_break_exit(monkeypatch):
    """If ``log_event`` raises (disk full, permissions, etc), the actual
    /exit send must still complete and return 0 — observability is
    best-effort, the kill is load-bearing.
    """
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@worker-cron-1")

    with patch(
        "metasphere.cli.session._resolve_session",
        return_value="metasphere-worker-cron-1",
    ), patch(
        "metasphere.cli.session.session_alive", return_value=True
    ), patch(
        "metasphere.cli.session._tmux",
        return_value=type("R", (), {"returncode": 0, "stdout": "", "stderr": ""})(),
    ), patch(
        "metasphere.cli.session.time.sleep", return_value=None
    ), patch(
        "metasphere.cli.session.log_event",
        side_effect=OSError("disk full"),
    ):
        rc = cli_session.main(["exit-self"])

    assert rc == 0


def test_exit_self_resolves_project_scoped_agent(monkeypatch):
    """Project-scoped agents must resolve to the project-prefixed session
    name, not the bare ``session_name_for`` form. Regression mirrors the
    bug class fixed in 107c792 for ``_check_deferred_command``'s resolver.
    """
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@accelerator-programs")

    sent_targets: list[str] = []

    def _record(*args):
        if args and args[0] == "send-keys" and "-t" in args:
            sent_targets.append(args[args.index("-t") + 1])

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        return R()

    rec = _agent_record("@accelerator-programs", project="research")

    # Drive the real ``_resolve_session`` so the project lookup is exercised.
    with patch(
        "metasphere.session.list_agents", return_value=[rec]
    ), patch(
        "metasphere.cli.session.session_alive", return_value=True
    ), patch(
        "metasphere.cli.session._tmux", side_effect=_record
    ), patch(
        "metasphere.cli.session.time.sleep", return_value=None
    ):
        rc = cli_session.main(["exit-self"])

    assert rc == 0
    assert sent_targets, "expected at least one send-keys -t <session>"
    expected = "metasphere-research-accelerator-programs"
    assert all(t == expected for t in sent_targets), (
        f"all send-keys must target project-scoped session {expected}; "
        f"got {sent_targets}"
    )
