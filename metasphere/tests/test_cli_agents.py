"""Tests for ``metasphere agent`` — list/status/spawn/wake CLI surface.

The list-tree behavior (KNOWN_ISSUES "Agent tree doesn't look like a
tree") groups persistent agents by project: global agents (empty
project sidecar) under a ``global/`` header, then each project's
agents under ``<project>/`` alphabetically. Within each bucket
agents are sorted by name.
"""

from __future__ import annotations

import pytest

from metasphere.cli import agents as cli_agents
from metasphere.paths import Paths


def _make_agent(d, *, mission: str = "m", project_sidecar: str = ""):
    d.mkdir(parents=True, exist_ok=True)
    (d / "MISSION.md").write_text(mission)
    (d / "scope").write_text("/")
    (d / "parent").write_text("@orchestrator")
    (d / "status").write_text("spawned")
    (d / "spawned_at").write_text("2026-04-07T00:00:00Z")
    if project_sidecar:
        (d / "project").write_text(project_sidecar)


def test_list_groups_global_first_then_projects(tmp_paths: Paths, capsys, monkeypatch):
    """Global agents (empty project) print under ``global/``; project-
    scoped agents print under ``<project>/`` headers, alphabetically."""
    # Force session_alive() False so output uses the dormant marker.
    monkeypatch.setattr("metasphere.agents.session_alive", lambda name: False)

    # Two global agents
    _make_agent(tmp_paths.agents / "@orchestrator")
    _make_agent(tmp_paths.agents / "@alice")
    # Two project-scoped agents under distinct projects
    _make_agent(tmp_paths.project_agents_dir("widget") / "@widget-eng")
    _make_agent(tmp_paths.project_agents_dir("widget") / "@widget-lead")
    _make_agent(tmp_paths.project_agents_dir("rho") / "@rho-eng")

    rc = cli_agents._list()
    out, _ = capsys.readouterr()
    assert rc == 0

    lines = [ln for ln in out.splitlines() if ln.strip()]

    # Header first
    assert lines[0] == "Persistent agents (have MISSION.md):"

    # global/ bucket appears before project buckets
    assert "  global/" in lines
    assert "  rho/" in lines
    assert "  widget/" in lines
    g_idx = lines.index("  global/")
    r_idx = lines.index("  rho/")
    w_idx = lines.index("  widget/")
    assert g_idx < r_idx < w_idx, (
        f"Expected global < rho < widget, got {g_idx} {r_idx} {w_idx}"
    )

    # Each agent prints with 4-space indent + dormant marker
    assert "    ○ @alice" in lines
    assert "    ○ @orchestrator" in lines
    assert "    ○ @rho-eng" in lines
    assert "    ○ @widget-eng" in lines
    assert "    ○ @widget-lead" in lines


def test_list_omits_global_bucket_when_no_global_agents(
    tmp_paths: Paths, capsys, monkeypatch,
):
    """If every persistent agent is project-scoped, no ``global/``
    header should print — only the project buckets that actually have
    members."""
    monkeypatch.setattr("metasphere.agents.session_alive", lambda name: False)
    _make_agent(tmp_paths.project_agents_dir("widget") / "@widget-eng")

    rc = cli_agents._list()
    out, _ = capsys.readouterr()
    assert rc == 0
    assert "global/" not in out
    assert "  widget/" in out
    assert "    ○ @widget-eng" in out


def test_list_project_filter_still_works_under_tree_layout(
    tmp_paths: Paths, capsys, monkeypatch,
):
    """``metasphere agent list <project>`` continues to filter to a
    single project. The bucket header still prints so the format is
    consistent across filtered/unfiltered views."""
    monkeypatch.setattr("metasphere.agents.session_alive", lambda name: False)
    _make_agent(tmp_paths.agents / "@orchestrator")
    _make_agent(tmp_paths.project_agents_dir("widget") / "@widget-eng")
    _make_agent(tmp_paths.project_agents_dir("rho") / "@rho-eng")

    rc = cli_agents._list(project_filter="widget")
    out, _ = capsys.readouterr()
    assert rc == 0
    assert "@widget-eng" in out
    assert "@rho-eng" not in out
    assert "@orchestrator" not in out


def test_list_no_persistent_agents_prints_message(
    tmp_paths: Paths, capsys, monkeypatch,
):
    monkeypatch.setattr("metasphere.agents.session_alive", lambda name: False)
    rc = cli_agents._list()
    out, _ = capsys.readouterr()
    assert rc == 0
    assert "No persistent agents." in out


# ---------------------------------------------------------------------------
# spawn_main flag-shape rejection (mirrors df6812e project-init fix)
# ---------------------------------------------------------------------------

def test_spawn_main_help_prints_usage(capsys):
    rc = cli_agents.spawn_main(["--help"])
    out, _ = capsys.readouterr()
    assert rc == 0
    assert "metasphere agent spawn" in out


def test_spawn_main_short_help_prints_usage(capsys):
    rc = cli_agents.spawn_main(["-h"])
    out, _ = capsys.readouterr()
    assert rc == 0
    assert "metasphere agent spawn" in out


def test_spawn_main_rejects_flag_shaped_agent_name(
    tmp_paths: Paths, capsys, monkeypatch,
):
    """``metasphere agent spawn --typo /scope/ "task"`` previously
    persisted ``@--typo`` to disk — the same argv-leak class as the
    ghost ``--help`` project fixed in df6812e. The library-layer
    validator now refuses it; the CLI renders the ValueError as a
    clean stderr line + exit 2.
    """
    monkeypatch.setenv("METASPHERE_SPAWN_NO_EXEC", "1")
    rc = cli_agents.spawn_main(["--bogus", "/", "do thing", "@orchestrator"])
    _, err = capsys.readouterr()
    assert rc == 2
    assert "looks like a CLI flag" in err
    assert not (tmp_paths.agents / "@--bogus").exists()
    assert not (tmp_paths.agents / "--bogus").exists()


@pytest.mark.parametrize(
    "argv,subcmd,extra",
    [
        (["list", "--bogus"], "list", "--bogus"),
        (["list", "widget", "--bogus"], "list", "--bogus"),
        (["list", "--filter=foo"], "list", "--filter=foo"),
        (["status", "--bogus"], "status", "--bogus"),
        (["status", "trailing"], "status", "trailing"),
        (["specs", "--bogus"], "specs", "--bogus"),
        (["specs", "extra"], "specs", "extra"),
    ],
)
def test_read_side_rejects_unknown_args(
    tmp_paths: Paths, capsys, argv, subcmd, extra,
):
    """Read-only ``agent {list,status,specs}`` previously silently
    dropped unknown trailing args and returned rc=0 with the full
    output. A typo like ``agent list --filter=foo`` would print the
    unfiltered tree as if the filter applied. Now rejected as rc=2.
    """
    rc = cli_agents.main(argv)
    _, err = capsys.readouterr()
    assert rc == 2
    assert f"metasphere agent {subcmd}" in err
    assert extra in err


def test_spawn_rejects_flag_shaped_parent(tmp_paths: Paths, capsys, monkeypatch):
    """``metasphere agent spawn @x / task --bogus`` previously took
    ``--bogus`` as the parent and rc=0'd. Same trailing-arg leak class
    27dccc4 closed on the read-side commands; spawn was out of scope
    in that commit. Reproducer from events log 2026-05-19T08:10:13Z.
    """
    monkeypatch.setenv("METASPHERE_SPAWN_NO_EXEC", "1")
    rc = cli_agents.spawn_main(["@x", "/", "task", "--bogus"])
    _, err = capsys.readouterr()
    assert rc == 2
    assert "parent looks like a CLI flag" in err
    assert "--bogus" in err
    assert not (tmp_paths.agents / "@x").exists()


def test_spawn_rejects_trailing_after_parent(tmp_paths: Paths, capsys, monkeypatch):
    """5th+ positional past ``@parent`` was silently dropped pre-fix.
    Same shape as the wake-trailing guard in 27dccc4.
    """
    monkeypatch.setenv("METASPHERE_SPAWN_NO_EXEC", "1")
    rc = cli_agents.spawn_main(["@x", "/", "task", "@orchestrator", "extra"])
    _, err = capsys.readouterr()
    assert rc == 2
    assert "unexpected trailing argument: extra" in err
    assert not (tmp_paths.agents / "@x").exists()


def test_wake_rejects_trailing_flag(tmp_paths: Paths, capsys):
    """``metasphere agent wake @x "task" --bogus`` previously silently
    dropped the ``--bogus`` and proceeded with the wake. Now rejected
    before any tmux side effects.
    """
    rc = cli_agents.wake_main(["@x", "task", "--bogus"])
    _, err = capsys.readouterr()
    assert rc == 2
    assert "unexpected trailing flag: --bogus" in err


def test_wake_rejects_trailing_positional(tmp_paths: Paths, capsys):
    rc = cli_agents.wake_main(["@x", "task", "extra-positional"])
    _, err = capsys.readouterr()
    assert rc == 2
    assert "unexpected trailing argument: extra-positional" in err


def test_agent_seed_rejects_flag_shaped_name(
    tmp_paths: Paths, capsys,
):
    """``metasphere agent seed --spec foo @--bogus`` previously
    seeded a persona stack under ``@--bogus/`` — same argv-leak class
    as spawn. seed_agent now validates names; the CLI renders the
    ValueError as a clean stderr line + exit 2.
    """
    # Create a minimal spec so the lookup succeeds before seeding.
    spec_dir = tmp_paths.project_root / "specs" / "researcher"
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "config.md").write_text(
        "---\nname: researcher\nrole: researcher\ndescription: t\n"
        "sandbox: scoped\npersistent: true\n---\n"
    )
    (spec_dir / "SOUL.md").write_text("# {{agent_id}}\n")
    (spec_dir / "MISSION.md").write_text("# {{agent_id}}\n")

    rc = cli_agents.main(["seed", "--spec", "researcher", "@--bogus"])
    _, err = capsys.readouterr()
    assert rc == 2
    assert "looks like a CLI flag" in err
    assert not (tmp_paths.agents / "@--bogus").exists()
