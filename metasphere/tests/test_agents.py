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
    # polluted the parent's inbox with an orphan PINNED !task per
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

    # PR #21: env_export for the pane must include the feedback-modal
    # disable flags so the claude TUI doesn't show "How is Claude doing
    # this session?" for metasphere-spawned agent REPLs (the modal
    # captures input and caused stuck-paste accumulation 2026-04-16).
    send_keys_calls = [c for c in calls if "send-keys" in c]
    env_exports = [
        c for c in send_keys_calls
        if any("METASPHERE_AGENT_ID" in part for part in c)
    ]
    assert env_exports, f"expected an env-export send-keys call, got {send_keys_calls}"
    env_line = " ".join(env_exports[0])
    assert "CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY=1" in env_line
    assert "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1" in env_line


def test_wake_persistent_project_scoped_uses_project_cwd(tmp_paths: Paths, tmp_path: Path):
    # When a project-scoped agent has no explicit `scope` file but its
    # `project` file names a registered project, the tmux new-session
    # must use the project's filesystem path as cwd — not the harness
    # project_root. Otherwise the agent inherits the wrong
    # .claude/settings.local.json and crashes on startup.
    proj_path = tmp_path / "example-proj"
    proj_path.mkdir(parents=True)
    # Seed projects.json so get_project() resolves it. Post-PR #11,
    # load_project reads the canonical location only, so we also seed
    # ``~/.metasphere/projects/example-proj/project.json``.
    (tmp_paths.root / "projects.json").write_text(json.dumps(
        [{"name": "example-proj", "path": str(proj_path), "registered": "x"}]
    ))
    (proj_path / ".metasphere").mkdir(parents=True)
    canonical_pf = tmp_paths.projects / "example-proj" / "project.json"
    canonical_pf.parent.mkdir(parents=True, exist_ok=True)
    canonical_pf.write_text(json.dumps(
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


# ---------------------------------------------------------------------------
# contract_main (contract retrieval)
# ---------------------------------------------------------------------------


def test_contract_live_agent_with_contract(tmp_paths: Paths):
    """contract_main reads contract sidecar files from a live agent dir."""
    from metasphere.cli.agents import contract_main
    from io import StringIO

    agent_dir = tmp_paths.agents / "@test-auditor"
    agent_dir.mkdir(parents=True)
    (agent_dir / "status").write_text("working: auditing")
    (agent_dir / "task").write_text("audit the thing")
    (agent_dir / "parent").write_text("@orchestrator")
    (agent_dir / "spawned_at").write_text("2026-04-12T10:00:00Z")
    (agent_dir / "authority").write_text("Read-only. MAY NOT write.")
    (agent_dir / "responsibility").write_text("Produce REPORT.md")
    (agent_dir / "accountability").write_text("File exists with 3+ sections")

    import sys
    old_stdout = sys.stdout
    sys.stdout = captured = StringIO()
    try:
        rc = contract_main(["@test-auditor"])
    finally:
        sys.stdout = old_stdout
    output = captured.getvalue()

    assert rc == 0
    assert "DELEGATION CONTRACT for @test-auditor" in output
    assert "Read-only. MAY NOT write." in output
    assert "Produce REPORT.md" in output
    assert "File exists with 3+ sections" in output
    assert "(live agent dir:" in output


def test_contract_gcd_agent_from_log(tmp_paths: Paths):
    """contract_main falls back to the GC preservation log when agent dir
    is gone, and extracts contract from preserved sidecar sections."""
    from metasphere.cli.agents import contract_main
    from io import StringIO

    # Create a GC log with preserved sidecar sections (post-e3d6100 format)
    log_dir = tmp_paths.logs / "agents" / "_global"
    log_dir.mkdir(parents=True)
    log_file = log_dir / "@dead-auditor.log"
    log_file.write_text(
        "# @dead-auditor — 2026-04-12T12:00:00Z\n"
        "Status: complete: done\n"
        "Reason: completed\n\n"
        "--- task ---\n"
        "audit something\n"
        "--- status ---\n"
        "complete: done\n"
        "--- parent ---\n"
        "@orchestrator\n"
        "--- spawned_at ---\n"
        "2026-04-12T09:00:00Z\n"
        "--- authority ---\n"
        "Read files only.\n"
        "--- responsibility ---\n"
        "Ship a report.\n"
        "--- accountability ---\n"
        "Report has 5 sections.\n"
        "--- harness.md ---\n"
        "# Agent: @dead-auditor\n"
    )

    import sys
    old_stdout = sys.stdout
    sys.stdout = captured = StringIO()
    try:
        rc = contract_main(["@dead-auditor"])
    finally:
        sys.stdout = old_stdout
    output = captured.getvalue()

    assert rc == 0
    assert "DELEGATION CONTRACT for @dead-auditor" in output
    assert "Read files only." in output
    assert "Ship a report." in output
    assert "Report has 5 sections." in output
    assert "(from GC log:" in output


def test_contract_gcd_agent_harness_fallback(tmp_paths: Paths):
    """For agents GC'd before the sidecar-preserve fix, verify extracts
    contract from the rendered harness.md section."""
    from metasphere.cli.agents import contract_main
    from io import StringIO

    log_dir = tmp_paths.logs / "agents" / "_global"
    log_dir.mkdir(parents=True)
    log_file = log_dir / "@old-audit.log"
    log_file.write_text(
        "# @old-audit — 2026-04-11T20:00:00Z\n"
        "Status: complete: done\n"
        "Reason: completed\n\n"
        "--- task ---\n"
        "old audit task\n"
        "--- status ---\n"
        "complete: done\n"
        "--- harness.md ---\n"
        "# Agent: @old-audit\n\n"
        "## Delegation Contract\n\n"
        "### Authority (what you MAY do)\n\n"
        "Only read.\n\n"
        "### Responsibility (what you MUST produce)\n\n"
        "A findings doc.\n\n"
        "### Accountability (how parent will verify)\n\n"
        "Doc has intro + 2 sections.\n\n"
        "---\n\n"
        "You are autonomous.\n"
    )

    import sys
    old_stdout = sys.stdout
    sys.stdout = captured = StringIO()
    try:
        rc = contract_main(["@old-audit"])
    finally:
        sys.stdout = old_stdout
    output = captured.getvalue()

    assert rc == 0
    assert "Only read." in output
    assert "A findings doc." in output
    assert "Doc has intro + 2 sections." in output


def test_contract_nonexistent_returns_error(tmp_paths: Paths):
    """contract_main returns 1 when no agent dir or log exists."""
    from metasphere.cli.agents import contract_main
    rc = contract_main(["@ghost"])
    assert rc == 1
