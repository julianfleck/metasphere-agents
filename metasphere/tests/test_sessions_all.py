"""Tests for the multi-agent viewer (``metasphere sessions all``).

Stubs ``subprocess.run`` so tmux is never actually invoked, and stubs
``list_agents`` / ``session_alive`` where helpful to isolate the build
logic.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from metasphere import session as sessmod
from metasphere.agents import AgentRecord
from metasphere.paths import Paths


def _make_agent(name: str, agent_dir: Path, project: str = "") -> AgentRecord:
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "MISSION.md").write_text("mission")
    (agent_dir / "scope").write_text("/repo")
    (agent_dir / "parent").write_text("@orchestrator")
    (agent_dir / "status").write_text("running")
    (agent_dir / "spawned_at").write_text("2026-04-11T00:00:00Z")
    return AgentRecord(
        name=name,
        scope="/repo",
        parent="@orchestrator",
        status="running",
        spawned_at="2026-04-11T00:00:00Z",
        mission_path=agent_dir / "MISSION.md",
        agent_dir=agent_dir,
        project=project,
    )


def test_list_alive_persistent_agents_filters_non_alive(tmp_paths: Paths):
    # Global persistent agent
    global_agent = _make_agent("@orchestrator", tmp_paths.agents / "@orchestrator")
    # Project-scoped persistent agent
    proj_agents_dir = tmp_paths.projects / "acme" / "agents"
    proj_agent = _make_agent(
        "@acme",
        proj_agents_dir / "@acme",
        project="acme",
    )
    # Ephemeral (no MISSION.md) — must be ignored
    ephemeral = tmp_paths.agents / "@ephem"
    ephemeral.mkdir(parents=True)
    (ephemeral / "scope").write_text("/repo")

    alive_names = {"metasphere-orchestrator"}  # only orchestrator is alive

    def fake_alive(name: str) -> bool:
        return name in alive_names

    with patch("metasphere.session.session_alive", side_effect=fake_alive):
        out = sessmod.list_alive_persistent_agents(tmp_paths)

    assert [a.name for a, _ in out] == ["@orchestrator"]
    assert out[0][1] == "metasphere-orchestrator"


def test_list_alive_persistent_agents_includes_project_scoped(tmp_paths: Paths):
    _make_agent("@orchestrator", tmp_paths.agents / "@orchestrator")
    _make_agent(
        "@acme",
        tmp_paths.projects / "acme" / "agents" / "@acme",
        project="acme",
    )

    with patch("metasphere.session.session_alive", return_value=True):
        out = sessmod.list_alive_persistent_agents(tmp_paths)

    by_name = {a.name: sname for a, sname in out}
    assert by_name["@orchestrator"] == "metasphere-orchestrator"
    # Project-scoped agents use the ``metasphere-<project>-<name>`` form.
    assert by_name["@acme"] == "metasphere-acme-acme"


class _TmuxRecorder:
    """Stand-in for ``subprocess.run`` that records the tmux commands
    issued and returns success for every call."""

    def __init__(self, alive: set[str] | None = None):
        self.calls: list[list[str]] = []
        self.alive = set(alive or ())

    def __call__(self, argv, *args, **kwargs):
        # Record the argv minus the tmux binary.
        self.calls.append(list(argv[1:]))

        class R:
            returncode = 0
            stdout = ""
            stderr = ""

        # ``has-session`` is how ``session_alive`` probes. Honour the
        # caller-provided ``alive`` set so we can simulate existing
        # viewer sessions for the idempotency test.
        if len(argv) >= 2 and argv[1] == "has-session":
            target = argv[argv.index("-t") + 1]
            R.returncode = 0 if target in self.alive else 1
        return R()


def test_build_viewer_session_no_agents_returns_empty(tmp_paths: Paths):
    with patch("metasphere.session.list_alive_persistent_agents", return_value=[]), \
         patch("metasphere.session.session_alive", return_value=False), \
         patch("metasphere.session.subprocess.run") as run_mock:
        viewer, linked = sessmod.build_viewer_session(paths=tmp_paths)
    assert viewer == sessmod.VIEWER_SESSION_NAME
    assert linked == []
    # Nothing should have been sent to tmux when there's no one to show.
    run_mock.assert_not_called()


def test_build_viewer_session_links_each_alive_agent(tmp_paths: Paths):
    a1 = _make_agent("@orchestrator", tmp_paths.agents / "@orchestrator")
    a2 = _make_agent(
        "@acme",
        tmp_paths.projects / "acme" / "agents" / "@acme",
        project="acme",
    )
    fake_alive_list = [
        (a1, "metasphere-orchestrator"),
        (a2, "metasphere-acme-acme"),
    ]
    recorder = _TmuxRecorder()

    with patch(
        "metasphere.session.list_alive_persistent_agents",
        return_value=fake_alive_list,
    ), patch(
        "metasphere.session.session_alive",
        return_value=False,  # no pre-existing viewer
    ), patch(
        "metasphere.session.subprocess.run",
        side_effect=recorder,
    ):
        viewer, linked = sessmod.build_viewer_session(paths=tmp_paths)

    assert viewer == "metasphere-all"
    assert [a.name for a in linked] == ["@orchestrator", "@acme"]

    # Verify the tmux script: new-session placeholder, two link-windows,
    # kill-window placeholder, select-window.
    cmds = [c[0] for c in recorder.calls]
    assert cmds.count("new-session") == 1
    assert cmds.count("link-window") == 2
    assert cmds.count("kill-window") == 1  # placeholder removal
    assert cmds.count("select-window") == 1

    # Each source appears as a link-window source arg.
    link_args = [c for c in recorder.calls if c[0] == "link-window"]
    sources = {c[c.index("-s") + 1] for c in link_args}
    assert sources == {
        "metasphere-orchestrator:0",
        "metasphere-acme-acme:0",
    }
    # Linked into the viewer session.
    dests = {c[c.index("-t") + 1] for c in link_args}
    assert dests == {"metasphere-all:1", "metasphere-all:2"}


def test_build_viewer_session_is_idempotent(tmp_paths: Paths):
    a1 = _make_agent("@orchestrator", tmp_paths.agents / "@orchestrator")
    # Simulate an existing viewer session that must be killed first.
    recorder = _TmuxRecorder(alive={"metasphere-all"})

    with patch(
        "metasphere.session.list_alive_persistent_agents",
        return_value=[(a1, "metasphere-orchestrator")],
    ), patch(
        "metasphere.session.session_alive",
        side_effect=lambda name: name == "metasphere-all",
    ), patch(
        "metasphere.session.subprocess.run",
        side_effect=recorder,
    ):
        viewer, linked = sessmod.build_viewer_session(paths=tmp_paths)

    # The stale viewer must have been killed before rebuild.
    kill_session_calls = [c for c in recorder.calls if c[:2] == ["kill-session", "-t"]]
    assert any(c[2] == "metasphere-all" for c in kill_session_calls)
    assert [a.name for a in linked] == ["@orchestrator"]


def test_build_viewer_session_empties_out_when_all_links_fail(tmp_paths: Paths):
    a1 = _make_agent("@orchestrator", tmp_paths.agents / "@orchestrator")

    class _FailingLinker(_TmuxRecorder):
        def __call__(self, argv, *args, **kwargs):
            r = super().__call__(argv, *args, **kwargs)
            if len(argv) >= 2 and argv[1] == "link-window":
                r.returncode = 1
            return r

    recorder = _FailingLinker()
    with patch(
        "metasphere.session.list_alive_persistent_agents",
        return_value=[(a1, "metasphere-orchestrator")],
    ), patch(
        "metasphere.session.session_alive",
        return_value=False,
    ), patch(
        "metasphere.session.subprocess.run",
        side_effect=recorder,
    ):
        viewer, linked = sessmod.build_viewer_session(paths=tmp_paths)

    assert linked == []
    # Viewer torn down after empty link run.
    kill_session_calls = [c for c in recorder.calls if c[:2] == ["kill-session", "-t"]]
    assert any(c[2] == "metasphere-all" for c in kill_session_calls)


def test_kill_viewer_session_noop_when_missing():
    with patch("metasphere.session.session_alive", return_value=False), \
         patch("metasphere.session.subprocess.run") as run_mock:
        assert sessmod.kill_viewer_session() is False
    run_mock.assert_not_called()


def test_kill_viewer_session_kills_when_alive():
    recorder = _TmuxRecorder(alive={"metasphere-all"})
    with patch(
        "metasphere.session.session_alive",
        side_effect=lambda name: name == "metasphere-all",
    ), patch(
        "metasphere.session.subprocess.run",
        side_effect=recorder,
    ):
        assert sessmod.kill_viewer_session() is True
    assert any(c[:2] == ["kill-session", "-t"] for c in recorder.calls)


def test_attach_viewer_missing_returns_1():
    with patch("metasphere.session.session_alive", return_value=False):
        assert sessmod.attach_viewer() == 1
