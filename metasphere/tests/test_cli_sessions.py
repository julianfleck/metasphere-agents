"""Tests for ``metasphere sessions`` — multi-agent tmux viewer dispatch.

Covers ``cli/sessions.py``: subcommand dispatch (``all`` / ``list`` /
``kill-viewer``), help paths, unknown-subcommand handling, and the
no-alive-agents edge.

The underlying tmux operations in ``metasphere.session`` are not
exercised here — these tests stub the imported entrypoints so the
dispatcher's behavior is the only thing under test.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from metasphere.cli import sessions as sessions_mod


@dataclass
class _FakeAgent:
    name: str
    project: str | None = None


def _run(argv, capsys):
    rc = sessions_mod.main(argv)
    out, err = capsys.readouterr()
    return rc, out, err


# --- help / dispatch ---------------------------------------------------

def test_no_args_prints_help_returns_2(capsys):
    rc, out, _ = _run([], capsys)
    assert rc == 2
    assert "metasphere sessions" in out
    assert "all" in out
    assert "list" in out
    assert "kill-viewer" in out


def test_help_flag_returns_0(capsys):
    rc, out, _ = _run(["--help"], capsys)
    assert rc == 0
    assert "metasphere sessions" in out
    assert "all" in out


def test_short_help_flag_returns_0(capsys):
    rc, _, _ = _run(["-h"], capsys)
    assert rc == 0


def test_unknown_subcommand_returns_2(capsys):
    rc, _, err = _run(["bogusquux"], capsys)
    assert rc == 2
    assert "unknown subcommand: bogusquux" in err


def test_ls_alias_routes_to_list(monkeypatch, capsys):
    monkeypatch.setattr(
        sessions_mod, "list_alive_persistent_agents", lambda: []
    )
    rc, out, _ = _run(["ls"], capsys)
    assert rc == 0
    assert "no alive persistent agents" in out


# --- sessions list -----------------------------------------------------

def test_list_with_no_agents(monkeypatch, capsys):
    monkeypatch.setattr(
        sessions_mod, "list_alive_persistent_agents", lambda: []
    )
    rc, out, _ = _run(["list"], capsys)
    assert rc == 0
    assert "(no alive persistent agents)" in out


def test_list_with_agents(monkeypatch, capsys):
    monkeypatch.setattr(
        sessions_mod,
        "list_alive_persistent_agents",
        lambda: [
            (_FakeAgent(name="@orchestrator"), "metasphere-orchestrator"),
            (_FakeAgent(name="@widget-eng", project="widget"),
             "metasphere-widget-widget-eng"),
        ],
    )
    rc, out, _ = _run(["list"], capsys)
    assert rc == 0
    assert "@orchestrator" in out
    assert "metasphere-orchestrator" in out
    # Project tag rendered for project-scoped agents.
    assert "@widget-eng [widget]" in out
    assert "metasphere-widget-widget-eng" in out


# --- sessions kill-viewer ---------------------------------------------

def test_kill_viewer_when_present(monkeypatch, capsys):
    monkeypatch.setattr(sessions_mod, "kill_viewer_session", lambda: True)
    rc, out, _ = _run(["kill-viewer"], capsys)
    assert rc == 0
    assert sessions_mod.VIEWER_SESSION_NAME in out
    assert "killed viewer session" in out


def test_kill_viewer_when_absent(monkeypatch, capsys):
    monkeypatch.setattr(sessions_mod, "kill_viewer_session", lambda: False)
    rc, _, err = _run(["kill-viewer"], capsys)
    assert rc == 1
    assert sessions_mod.VIEWER_SESSION_NAME in err
    assert "no viewer session" in err


# --- sessions all ------------------------------------------------------

def test_all_with_no_agents_returns_1(monkeypatch, capsys):
    monkeypatch.setattr(
        sessions_mod, "build_viewer_session",
        lambda: (sessions_mod.VIEWER_SESSION_NAME, []),
    )
    # attach_viewer must NOT be called when nothing is linked.
    def _boom(_v):
        raise AssertionError("attach_viewer called with no linked agents")
    monkeypatch.setattr(sessions_mod, "attach_viewer", _boom)

    rc, _, err = _run(["all"], capsys)
    assert rc == 1
    assert "no alive persistent agents to attach" in err


def test_all_with_agents_attaches(monkeypatch, capsys):
    linked = [
        _FakeAgent(name="@orchestrator"),
        _FakeAgent(name="@widget-eng", project="widget"),
    ]
    monkeypatch.setattr(
        sessions_mod, "build_viewer_session",
        lambda: (sessions_mod.VIEWER_SESSION_NAME, linked),
    )
    captured: dict = {}

    def _attach(viewer):
        captured["viewer"] = viewer
        return 0

    monkeypatch.setattr(sessions_mod, "attach_viewer", _attach)

    rc, out, _ = _run(["all"], capsys)
    assert rc == 0
    assert captured["viewer"] == sessions_mod.VIEWER_SESSION_NAME
    assert "Attaching 2 agents" in out
    assert "@orchestrator" in out
    assert "@widget-eng" in out


def test_all_returns_attach_rc(monkeypatch, capsys):
    """If ``attach_viewer`` returns non-zero (e.g. viewer vanished
    between build and attach), surface that as the dispatch rc."""
    monkeypatch.setattr(
        sessions_mod, "build_viewer_session",
        lambda: (sessions_mod.VIEWER_SESSION_NAME,
                 [_FakeAgent(name="@x")]),
    )
    monkeypatch.setattr(sessions_mod, "attach_viewer", lambda _v: 1)
    rc, _, _ = _run(["all"], capsys)
    assert rc == 1
