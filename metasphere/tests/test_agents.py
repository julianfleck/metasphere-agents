"""Tests for metasphere.agents (spawn + wake lifecycle module)."""

from __future__ import annotations

import subprocess
import json
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from metasphere import agents
from metasphere.paths import Paths


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------

def test_session_name_for_strips_at_prefix():
    assert agents.session_name_for("@briefing") == "metasphere-briefing"
    assert agents.session_name_for("briefing") == "metasphere-briefing"


def test_list_agents_finds_created_agents(tmp_paths: Paths):
    a1 = tmp_paths.agents / "@alpha"
    a2 = tmp_paths.agents / "@beta"
    for d in (a1, a2):
        d.mkdir(parents=True)
        (d / "scope").write_text(str(tmp_paths.project_root))
        (d / "parent").write_text("@orchestrator")
        (d / "status").write_text("spawned")
        (d / "spawned_at").write_text("2026-04-07T00:00:00Z")
    (a1 / "MISSION.md").write_text("alpha mission")

    found = agents.list_agents(tmp_paths)
    names = [a.name for a in found]
    assert "@alpha" in names
    assert "@beta" in names


def test_is_persistent_requires_mission(tmp_paths: Paths):
    d = tmp_paths.agents / "@persistent"
    d.mkdir(parents=True)
    (d / "MISSION.md").write_text("be a thing")
    rec = agents._agent_record_from_dir(d)
    assert agents.is_persistent(rec) is True

    d2 = tmp_paths.agents / "@ephemeral"
    d2.mkdir(parents=True)
    rec2 = agents._agent_record_from_dir(d2)
    assert agents.is_persistent(rec2) is False


# ---------------------------------------------------------------------------
# spawn_ephemeral
# ---------------------------------------------------------------------------

def test_spawn_ephemeral_writes_files_and_skips_exec(tmp_paths: Paths, monkeypatch):
    monkeypatch.setenv("METASPHERE_SPAWN_NO_EXEC", "1")

    with patch("metasphere.agents.subprocess.Popen") as popen_mock:
        rec = agents.spawn_ephemeral(
            "@spawnling",
            "/sub/",
            "do the thing",
            parent="@orchestrator",
            paths=tmp_paths,
        )
        popen_mock.assert_not_called()

    agent_dir = tmp_paths.agents / "@spawnling"
    assert (agent_dir / "task").read_text().strip() == "do the thing"
    assert (agent_dir / "status").read_text().startswith("spawned:")
    assert (agent_dir / "parent").read_text().strip() == "@orchestrator"
    assert (agent_dir / "harness.md").is_file()
    assert "do the thing" in (agent_dir / "harness.md").read_text()
    assert rec.name == "@spawnling"


def test_spawn_ephemeral_contract_fields_persist_and_render(tmp_paths: Paths, monkeypatch):
    # Contract-first delegation: authority/responsibility/accountability
    # are persisted to the agent dir and rendered into the harness so
    # the spawned agent can see them up front.
    monkeypatch.setenv("METASPHERE_SPAWN_NO_EXEC", "1")

    rec = agents.spawn_ephemeral(
        "@contractor",
        "/",
        "fix the thing",
        parent="@orchestrator",
        paths=tmp_paths,
        authority="Read/write metasphere/consolidate.py and its test file only.",
        responsibility="Ship a commit that stops UNOWNED re-escalation after N pings.",
        accountability="I will re-run pytest and grep for noop-pinged-out in events.",
    )

    agent_dir = tmp_paths.agents / "@contractor"
    # Persisted to disk
    assert (agent_dir / "authority").read_text().strip() == (
        "Read/write metasphere/consolidate.py and its test file only."
    )
    assert (agent_dir / "responsibility").read_text().strip() == (
        "Ship a commit that stops UNOWNED re-escalation after N pings."
    )
    assert (agent_dir / "accountability").read_text().strip() == (
        "I will re-run pytest and grep for noop-pinged-out in events."
    )
    # Rendered into the harness as a contract block
    harness = (agent_dir / "harness.md").read_text()
    assert "Delegation Contract" in harness
    assert "Authority (what you MAY do)" in harness
    assert "Responsibility (what you MUST produce)" in harness
    assert "Accountability (how parent will verify)" in harness
    assert "Read/write metasphere/consolidate.py" in harness


def test_spawn_ephemeral_legacy_no_contract_still_works(tmp_paths: Paths, monkeypatch):
    # Back-compat: spawning without any contract fields produces a
    # harness with no Delegation Contract block, and the agent dir has
    # no authority/responsibility/accountability sidecar files.
    monkeypatch.setenv("METASPHERE_SPAWN_NO_EXEC", "1")

    agents.spawn_ephemeral(
        "@legacy", "/", "prose task", parent="@orchestrator", paths=tmp_paths,
    )

    agent_dir = tmp_paths.agents / "@legacy"
    harness = (agent_dir / "harness.md").read_text()
    assert "Delegation Contract" not in harness
    assert not (agent_dir / "authority").exists()
    assert not (agent_dir / "responsibility").exists()
    assert not (agent_dir / "accountability").exists()


def test_spawn_ephemeral_normalizes_unprefixed_name(tmp_paths: Paths, monkeypatch):
    monkeypatch.setenv("METASPHERE_SPAWN_NO_EXEC", "1")
    rec = agents.spawn_ephemeral("noprefix", "/", "task", paths=tmp_paths)
    assert rec.name == "@noprefix"
    assert (tmp_paths.agents / "@noprefix").is_dir()


def test_spawn_ephemeral_does_not_pollute_scope_inbox(tmp_paths: Paths, monkeypatch):
    # Regression: a previous version sent an initial `!task` message
    # into the scope inbox alongside the harness, claiming to "let the
    # agent see the task". The harness already embeds the task in its
    # `Your Task` section, so the message was redundant — and at
    # shared scopes (parent and child both at /) it permanently
    # polluted the parent's inbox with an orphan SACRED !task per
    # spawn. The send is now elided.
    monkeypatch.setenv("METASPHERE_SPAWN_NO_EXEC", "1")
    agents.spawn_ephemeral(
        "@quiet-spawn", "/", "do work", parent="@orchestrator", paths=tmp_paths,
    )
    # Scope-/ resolves to project_root in spawn_ephemeral.
    inbox = tmp_paths.project_root / ".messages" / "inbox"
    msgs = list(inbox.glob("*.msg")) if inbox.exists() else []
    assert msgs == [], f"spawn should not create scope-inbox messages, got {msgs}"


# ---------------------------------------------------------------------------
# wake_persistent
# ---------------------------------------------------------------------------

def _make_persistent(tmp_paths: Paths, name: str = "@waker") -> Path:
    d = tmp_paths.agents / name
    d.mkdir(parents=True)
    (d / "MISSION.md").write_text("mission")
    (d / "scope").write_text(str(tmp_paths.project_root))
    return d


def test_wake_persistent_rejects_non_persistent(tmp_paths: Paths):
    (tmp_paths.agents / "@nope").mkdir(parents=True)
    with pytest.raises(ValueError, match="not a persistent agent"):
        agents.wake_persistent("@nope", paths=tmp_paths)


def test_wake_persistent_cold_start_runs_tmux_new_session(tmp_paths: Paths):
    _make_persistent(tmp_paths)
    calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        # Pretend has-session returns nonzero (no session yet) and ready
        # marker is present so the wait loop exits fast.
        cp = MagicMock()
        if "has-session" in cmd:
            cp.returncode = 1
            cp.stdout = ""
        elif "capture-pane" in cmd:
            cp.returncode = 0
            cp.stdout = "bypass permissions on"
        else:
            cp.returncode = 0
            cp.stdout = ""
        cp.stderr = ""
        return cp

    with patch("metasphere.agents.subprocess.run", side_effect=fake_run):
        agents.wake_persistent("@waker", paths=tmp_paths)

    new_session_calls = [c for c in calls if "new-session" in c]
    assert new_session_calls, f"expected tmux new-session call, got {calls}"
    nc = new_session_calls[0]
    assert "-s" in nc
    assert "metasphere-waker" in nc


def test_wake_persistent_project_scoped_uses_project_cwd(tmp_paths: Paths, tmp_path: Path):
    # When a project-scoped agent has no explicit `scope` file but its
    # `project` file names a registered project, the tmux new-session
    # must use the project's filesystem path as cwd — not the harness
    # project_root. Otherwise the agent inherits the wrong
    # .claude/settings.local.json and crashes on startup.
    proj_path = tmp_path / "example-proj"
    proj_path.mkdir(parents=True)
    # Seed projects.json so get_project() resolves it.
    (tmp_paths.root / "projects.json").write_text(json.dumps(
        [{"name": "example-proj", "path": str(proj_path), "registered": "x"}]
    ))
    # Seed .metasphere/project.json so load_project succeeds.
    (proj_path / ".metasphere").mkdir(parents=True)
    (proj_path / ".metasphere" / "project.json").write_text(json.dumps(
        {"name": "example-proj", "path": str(proj_path), "goal": "", "members": []}
    ))
    # Create the project-scoped agent dir (not in global agents/).
    agent_dir = tmp_paths.projects / "example-proj" / "agents" / "@worker"
    agent_dir.mkdir(parents=True)
    (agent_dir / "MISSION.md").write_text("mission")
    (agent_dir / "project").write_text("example-proj")
    # NOTE: intentionally no `scope` file — this is the common case.

    calls: list[list[str]] = []

    def fake_run(cmd, *args, **kwargs):
        calls.append(list(cmd))
        cp = MagicMock()
        if "has-session" in cmd:
            cp.returncode = 1
            cp.stdout = ""
        elif "capture-pane" in cmd:
            cp.returncode = 0
            cp.stdout = "bypass permissions on"
        else:
            cp.returncode = 0
            cp.stdout = ""
        cp.stderr = ""
        return cp

    with patch("metasphere.agents.subprocess.run", side_effect=fake_run):
        agents.wake_persistent("@worker", paths=tmp_paths)

    new_session = next((c for c in calls if "new-session" in c), None)
    assert new_session, f"expected tmux new-session, got {calls}"
    # -c <cwd> should point at the project path, not the harness.
    cwd_idx = new_session.index("-c") + 1
    assert new_session[cwd_idx] == str(proj_path), (
        f"expected cwd={proj_path}, got {new_session[cwd_idx]}"
    )


def test_wake_persistent_already_alive_injects_task(tmp_paths: Paths):
    _make_persistent(tmp_paths)
    submitted: list[tuple[str, str]] = []

    def fake_run(cmd, *args, **kwargs):
        cp = MagicMock()
        cp.stdout = ""
        cp.stderr = ""
        if "has-session" in cmd:
            cp.returncode = 0  # alive
        else:
            cp.returncode = 0
        return cp

    def fake_tmux_submit(session: str, message: str) -> bool:
        submitted.append((session, message))
        return True

    with patch("metasphere.agents.subprocess.run", side_effect=fake_run), \
         patch("metasphere.agents._tmux_submit", side_effect=fake_tmux_submit):
        agents.wake_persistent("@waker", first_task="hello", paths=tmp_paths)

    assert submitted, "expected _tmux_submit call"
    assert submitted[0][0] == "metasphere-waker"
    assert "hello" in submitted[0][1]


# ---------------------------------------------------------------------------
# gc_dormant
# ---------------------------------------------------------------------------

def test_gc_dormant_returns_idle_agents(tmp_paths: Paths):
    _make_persistent(tmp_paths, "@idleone")
    _make_persistent(tmp_paths, "@freshone")

    def fake_run(cmd, *args, **kwargs):
        cp = MagicMock()
        cp.stdout = ""
        cp.stderr = ""
        if "has-session" in cmd:
            cp.returncode = 0  # both alive
        elif "display-message" in cmd:
            session = cmd[cmd.index("-t") + 1]
            now_minus_two_days = "1000000000"  # very old
            now_recent = str(2_000_000_000)  # future-ish, idle≈0
            cp.returncode = 0
            cp.stdout = now_minus_two_days if "idleone" in session else now_recent
        else:
            cp.returncode = 0
        return cp

    with patch("metasphere.agents.subprocess.run", side_effect=fake_run):
        dormant = agents.gc_dormant(paths=tmp_paths, max_idle_seconds=3600)

    assert "@idleone" in dormant
    assert "@freshone" not in dormant
