"""Tests for ``metasphere restart`` (metasphere/cli/restart.py).

Wraps the long-form recipe operators were hand-typing
(``systemctl --user restart metasphere-{heartbeat,gateway,schedule}`` +
``tmux kill-session`` + ``metasphere agent wake``).
"""

from __future__ import annotations

from unittest import mock

import pytest

from metasphere.cli import restart as R


def test_help_short_flag_returns_zero(capsys):
    rc = R.main(["-h"])
    out, _ = capsys.readouterr()
    assert rc == 0
    assert "metasphere restart" in out


def test_too_many_args_returns_usage(capsys):
    rc = R.main(["@a", "@b", "@c"])
    _, err = capsys.readouterr()
    assert rc == 2
    assert "usage:" in err


def test_no_args_restarts_daemons_and_alive_agents(capsys, tmp_path, monkeypatch):
    """Bare ``metasphere restart`` restarts all three systemd daemons
    AND every alive persistent agent's tmux session."""
    sysctl_calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(R, "_systemctl",
                        lambda *args: sysctl_calls.append(args) or 0)

    # Stub the agent-listing path: pretend two persistent agents are alive.
    fake_alive = ["@metasphere-eng", "@orchestrator"]
    monkeypatch.setattr(R, "_alive_persistent_agent_ids",
                        lambda paths: fake_alive)

    restart_log: list[str] = []

    def fake_restart_agent(agent_name, paths):
        restart_log.append(agent_name)
        return True, f"{agent_name}: restarted"

    monkeypatch.setattr(R, "_restart_agent_session", fake_restart_agent)
    monkeypatch.setattr(R, "_inside_orchestrator", lambda: False)

    # Avoid resolve() touching real ~/.metasphere
    monkeypatch.setattr(R, "resolve", lambda: mock.MagicMock())

    rc = R.main([])
    out, _ = capsys.readouterr()
    assert rc == 0

    # All three daemons restarted.
    restarted = {a[1] for a in sysctl_calls if a and a[0] == "restart"}
    assert restarted == set(R._DAEMONS)

    # Both agents got restart calls in the order returned (orchestrator
    # last by convention so it doesn't kill the caller before the rest
    # finish).
    assert restart_log == fake_alive
    assert "metasphere-heartbeat" in out
    assert "@metasphere-eng" in out
    assert "@orchestrator" in out


def test_no_args_warns_when_inside_orchestrator(capsys, monkeypatch):
    monkeypatch.setattr(R, "_systemctl", lambda *args: 0)
    monkeypatch.setattr(R, "_alive_persistent_agent_ids", lambda paths: [])
    monkeypatch.setattr(R, "_inside_orchestrator", lambda: True)
    monkeypatch.setattr(R, "resolve", lambda: mock.MagicMock())

    R.main([])
    _, err = capsys.readouterr()
    assert "WARNING" in err
    assert "@orchestrator" in err
    assert "kill this process" in err


def test_no_args_returns_nonzero_when_a_daemon_fails(capsys, monkeypatch):
    """If systemctl restart returns nonzero for any daemon, the overall
    exit code is 1."""
    def fake_systemctl(*args):
        # heartbeat fails, gateway+schedule succeed
        if args == ("restart", "metasphere-heartbeat"):
            return 1
        return 0
    monkeypatch.setattr(R, "_systemctl", fake_systemctl)
    monkeypatch.setattr(R, "_alive_persistent_agent_ids", lambda paths: [])
    monkeypatch.setattr(R, "_inside_orchestrator", lambda: False)
    monkeypatch.setattr(R, "resolve", lambda: mock.MagicMock())

    rc = R.main([])
    out, _ = capsys.readouterr()
    assert rc == 1
    assert "metasphere-heartbeat: FAILED" in out
    assert "metasphere-gateway: ok" in out


def test_one_arg_unknown_agent_suggests_near_matches(capsys, monkeypatch):
    """Unknown agent name → rc=2 with a 'did you mean' suggestion."""
    fake_agents = [
        mock.MagicMock(name="@metasphere-eng"),
        mock.MagicMock(name="@metasphere-critic"),
        mock.MagicMock(name="@writing-lead"),
    ]
    for fa, n in zip(fake_agents,
                     ["@metasphere-eng", "@metasphere-critic", "@writing-lead"]):
        fa.name = n
    monkeypatch.setattr(R._agents, "list_agents", lambda paths: fake_agents)
    monkeypatch.setattr(R, "resolve", lambda: mock.MagicMock())

    rc = R.main(["@metasphere-engg"])  # typo
    _, err = capsys.readouterr()
    assert rc == 2
    assert "unknown agent" in err
    assert "did you mean" in err
    assert "metasphere-eng" in err


def test_one_arg_known_agent_kills_and_respawns(capsys, monkeypatch):
    fake_agents = [mock.MagicMock()]
    fake_agents[0].name = "@metasphere-eng"
    monkeypatch.setattr(R._agents, "list_agents", lambda paths: fake_agents)
    monkeypatch.setattr(R, "resolve", lambda: mock.MagicMock())

    captured: list[tuple[str, object]] = []
    def fake_restart_agent(agent, paths):
        captured.append((agent, paths))
        return True, f"{agent}: tmux killed + wake_persistent ok"
    monkeypatch.setattr(R, "_restart_agent_session", fake_restart_agent)

    rc = R.main(["@metasphere-eng"])
    out, _ = capsys.readouterr()
    assert rc == 0
    assert len(captured) == 1
    assert captured[0][0] == "@metasphere-eng"
    assert "wake_persistent ok" in out


def test_restart_agent_uses_project_scoped_session(monkeypatch):
    """Regression: ``metasphere restart @<project-scoped-agent>`` must
    look up the project-aware tmux session name. With bare-name
    resolution the existing session goes undetected, the kill is
    skipped, and ``wake_persistent`` either spawns a duplicate or
    fails on the conflict. Sister-fix to the posthook deferred-cmd
    resolution bug.
    """
    from metasphere.agents import AgentRecord
    from metasphere.cli import restart as R

    rec = AgentRecord(
        name="@brand-mentions",
        scope="",
        parent="",
        status="",
        spawned_at="",
        project="research",
    )

    sessions_checked: list[str] = []
    sessions_killed: list[str] = []

    def fake_alive(name: str) -> bool:
        sessions_checked.append(name)
        return True  # pretend the project-scoped session is up

    def fake_kill(name: str) -> bool:
        sessions_killed.append(name)
        return True

    def fake_wake(agent_id, paths=None):
        return None

    paths = mock.MagicMock()
    monkeypatch.setattr("metasphere.session.list_agents", lambda: [rec])
    monkeypatch.setattr(R._agents, "session_alive", fake_alive)
    monkeypatch.setattr(R._agents, "wake_persistent", fake_wake)
    monkeypatch.setattr(R, "_tmux_kill", fake_kill)

    ok, msg = R._restart_agent_session("@brand-mentions", paths)

    assert ok is True
    assert sessions_checked == ["metasphere-research-brand-mentions"], (
        f"expected project-aware session name, got {sessions_checked!r}"
    )
    assert sessions_killed == ["metasphere-research-brand-mentions"], (
        f"kill must target the same project-aware session, "
        f"got {sessions_killed!r}"
    )


def test_one_arg_normalizes_bare_name(capsys, monkeypatch):
    """``metasphere restart metasphere-eng`` (no @ prefix) should
    normalize to ``@metasphere-eng`` and find the agent."""
    fake_agents = [mock.MagicMock()]
    fake_agents[0].name = "@metasphere-eng"
    monkeypatch.setattr(R._agents, "list_agents", lambda paths: fake_agents)
    monkeypatch.setattr(R, "resolve", lambda: mock.MagicMock())

    captured: list[str] = []
    monkeypatch.setattr(R, "_restart_agent_session",
                        lambda a, p: (captured.append(a) or (True, "ok")))

    rc = R.main(["metasphere-eng"])
    assert rc == 0
    assert captured == ["@metasphere-eng"]


def test_alive_persistent_puts_orchestrator_last(monkeypatch, tmp_path):
    """If @orchestrator is among the alive set, it must be restarted
    LAST so that running this from inside the orchestrator pane doesn't
    kill the process before earlier agents are rebuilt."""
    fake_records = []
    for n in ("@writing-lead", "@orchestrator", "@metasphere-eng"):
        fa = mock.MagicMock()
        fa.name = n
        fa.is_persistent = True
        fa.session_name = f"metasphere-{n[1:]}"
        fake_records.append(fa)
    monkeypatch.setattr(R._agents, "list_agents", lambda paths: fake_records)
    monkeypatch.setattr(R._agents, "session_alive", lambda name: True)

    # The gateway-named orchestrator session must also be reported alive.
    import metasphere.gateway.session as _gs
    monkeypatch.setattr(_gs, "session_alive", lambda name=_gs.SESSION_NAME: True)

    out = R._alive_persistent_agent_ids(paths=mock.MagicMock())
    assert out[-1] == "@orchestrator", (
        f"@orchestrator must be last in restart order, got: {out}"
    )


def test_inside_orchestrator_detection_via_env(monkeypatch):
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@orchestrator")
    monkeypatch.delenv("TMUX", raising=False)
    assert R._inside_orchestrator() is True

    monkeypatch.setenv("METASPHERE_AGENT_ID", "@metasphere-eng")
    assert R._inside_orchestrator() is False


def test_inside_orchestrator_detection_via_tmux_env(monkeypatch):
    monkeypatch.delenv("METASPHERE_AGENT_ID", raising=False)
    monkeypatch.setenv("TMUX", "/tmp/tmux-1000/default,123,metasphere-orchestrator")
    assert R._inside_orchestrator() is True


def test_systemctl_missing_returns_one(monkeypatch):
    """If systemctl isn't on PATH (Mac without launchctl, sandbox, etc.),
    _systemctl returns 1 — restart_daemon then reports FAILED rather
    than pretending success."""
    monkeypatch.setattr(R.shutil, "which", lambda x: None if x == "systemctl" else "/usr/bin/" + x)
    assert R._systemctl("restart", "anything") == 1
    assert R._restart_daemon("metasphere-gateway") is False
