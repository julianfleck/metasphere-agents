"""Tests for ``metasphere ls`` — landscape + per-agent views."""

from __future__ import annotations

import json

import pytest

from metasphere.cli import ls as ls_mod
from metasphere.cli import main as main_mod


def test_ls_help_returns_zero(capsys):
    rc = ls_mod.main(["--help"])
    out, _ = capsys.readouterr()
    assert rc == 0
    assert "Usage: metasphere ls" in out


def test_ls_empty_environment_does_not_crash(tmp_paths, capsys, monkeypatch):
    """With a pristine metasphere home, ``ls`` should still render."""
    # Force session_alive() to False to avoid touching real tmux.
    monkeypatch.setattr("metasphere.agents.session_alive", lambda name: False)
    rc = ls_mod.main([])
    out, _ = capsys.readouterr()
    assert rc == 0
    assert "Metasphere" in out
    assert "Projects" in out
    assert "Events" in out
    assert "Agents" in out
    assert "Tasks" in out


def test_ls_registered_project_shows_counts(tmp_paths, capsys, monkeypatch):
    """A project listed in projects.json and present on disk is rendered
    with the ``(N tasks, M agents)`` suffix, not the ``(missing)`` flag."""
    monkeypatch.setattr("metasphere.agents.session_alive", lambda name: False)
    # Register one project whose path is the test scope (which exists).
    # Post-PR #11: project.json lives at canonical location only.
    proj_dir = tmp_paths.project_root
    (proj_dir / ".metasphere").mkdir(exist_ok=True)
    project_json = tmp_paths.projects / "demo" / "project.json"
    project_json.parent.mkdir(parents=True, exist_ok=True)
    project_json.write_text(json.dumps({
        "schema": 2,
        "name": "demo",
        "path": str(proj_dir),
        "status": "active",
    }))
    registry = tmp_paths.root / "projects.json"
    registry.write_text(json.dumps([{"name": "demo", "path": str(proj_dir)}]))

    rc = ls_mod.main([])
    out, _ = capsys.readouterr()
    assert rc == 0
    assert "demo" in out
    assert "(missing)" not in out


def test_ls_missing_project_renders_missing_marker(tmp_paths, capsys, monkeypatch):
    monkeypatch.setattr("metasphere.agents.session_alive", lambda name: False)
    registry = tmp_paths.root / "projects.json"
    registry.write_text(json.dumps([
        {"name": "ghost", "path": "/nonexistent/ghost-project"},
    ]))
    rc = ls_mod.main([])
    out, _ = capsys.readouterr()
    assert rc == 0
    assert "ghost" in out
    assert "(missing)" in out


def test_ls_agent_view_missing_agent(tmp_paths, capsys):
    rc = ls_mod.main(["@does-not-exist"])
    _, err = capsys.readouterr()
    assert rc == 1
    assert "not found" in err


def test_ls_agent_view_renders_status(tmp_paths, capsys, monkeypatch):
    monkeypatch.setattr("metasphere.agents.session_alive", lambda name: False)
    agent_dir = tmp_paths.agents / "@scout"
    agent_dir.mkdir(parents=True)
    (agent_dir / "status").write_text("active: investigating")
    (agent_dir / "task").write_text("find the thing")
    (agent_dir / "scope").write_text(str(tmp_paths.project_root))
    rc = ls_mod.main(["@scout"])
    out, _ = capsys.readouterr()
    assert rc == 0
    assert "@scout" in out
    assert "active: investigating" in out
    assert "find the thing" in out


def test_ls_agent_event_tail_filters_by_agent(tmp_paths, capsys, monkeypatch):
    """``ls @X`` pulls the last 5 events tagged with agent=X."""
    monkeypatch.setattr("metasphere.agents.session_alive", lambda name: False)
    agent_dir = tmp_paths.agents / "@scout"
    agent_dir.mkdir(parents=True)
    (agent_dir / "status").write_text("active: x")

    events_dir = tmp_paths.events
    events_dir.mkdir(parents=True)
    # Use a dated filename the events module will pick up.
    log = events_dir / "events-2026-04-14.jsonl"
    lines = []
    for i in range(3):
        lines.append(json.dumps({
            "timestamp": f"2026-04-14T10:{i:02d}:00Z",
            "type": "task.update",
            "agent": "@scout",
            "message": f"scout event {i}",
        }))
    # Inject an unrelated event that must be filtered out.
    lines.append(json.dumps({
        "timestamp": "2026-04-14T10:05:00Z",
        "type": "task.update",
        "agent": "@somebody-else",
        "message": "not for scout",
    }))
    log.write_text("\n".join(lines) + "\n")

    rc = ls_mod.main(["@scout"])
    out, _ = capsys.readouterr()
    assert rc == 0
    assert "scout event 0" in out or "scout event 2" in out
    assert "not for scout" not in out


def test_ls_dispatcher_registers_ls(capsys):
    """Regression: ``metasphere ls`` must not route to a not-ported stub."""
    assert main_mod.REGISTRY["ls"] == "metasphere.cli.ls:main"
    assert "not_ported" not in main_mod.REGISTRY["ls"]
