"""Tests for B7 — resolution-order chain in `_render_project_capsule`.

Precedence (highest first):
  1. MISSION.md frontmatter (``project:`` / ``projects:``).
  2. ``~/.metasphere/teams.yaml`` agent→projects roster.
  3. Path-nested inference (agent at
     ``~/.metasphere/projects/<P>/agents/@<id>/``).
  4. No capsule.

B4's name-prefix string-match inference was deleted in B7 — these
tests pin the brittleness fix in place.
"""

from __future__ import annotations

from pathlib import Path

from metasphere import context as ctx
from metasphere import teams as _teams
from metasphere.paths import Paths


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _write_teams_yaml(tmp_paths: Paths, content: str) -> None:
    _teams._CACHE.clear()
    (tmp_paths.root / "teams.yaml").write_text(content, encoding="utf-8")


def _seed_root_agent_mission(
    tmp_paths: Paths, agent: str, frontmatter: str = "",
) -> Path:
    d = tmp_paths.agent_dir(agent)
    d.mkdir(parents=True, exist_ok=True)
    fm_block = ("---\n" + frontmatter + "---\n\n") if frontmatter else ""
    (d / "MISSION.md").write_text(
        fm_block + "# Mission\n\nbody\n", encoding="utf-8",
    )
    return d


def _seed_project_nested_agent_mission(
    tmp_paths: Paths, project: str, agent: str, frontmatter: str = "",
) -> Path:
    d = tmp_paths.project_agent_dir(project, agent)
    d.mkdir(parents=True, exist_ok=True)
    fm_block = ("---\n" + frontmatter + "---\n\n") if frontmatter else ""
    (d / "MISSION.md").write_text(
        fm_block + "# Mission\n\nbody\n", encoding="utf-8",
    )
    return d


def _seed_project_file(
    tmp_paths: Paths, project: str,
    *, learnings: str = "", memory: str = "",
) -> Path:
    pdir = tmp_paths.projects / project
    pdir.mkdir(parents=True, exist_ok=True)
    if learnings:
        (pdir / "LEARNINGS.md").write_text(
            "## 2026-05-29: probe entry\n\n" + learnings + "\n",
            encoding="utf-8",
        )
    if memory:
        (pdir / "MEMORY.md").write_text(
            "## 2026-05-29: probe memory\n\n" + memory + "\n",
            encoding="utf-8",
        )
    return pdir


# ===========================================================================
# (b) teams.yaml — the brittleness fix
# ===========================================================================


def test_resolution_teams_yaml_brittleness_fix(tmp_paths: Paths):
    """Canonical case: ``@spot`` doesn't follow ``<project>-<role>``
    convention. teams.yaml resolves it where name-prefix inference
    cannot."""
    _seed_project_file(
        tmp_paths, "widget", learnings="hetzner anchor body",
    )
    _write_teams_yaml(tmp_paths, """
agents:
  spot:
    projects: [widget]
""")
    # Agent has NO MISSION.md at all — only teams.yaml describes them.
    out = ctx._render_project_capsule(tmp_paths, "@spot")
    assert "## Project: widget" in out
    assert "hetzner anchor body" in out


def test_resolution_teams_yaml_supports_multi_project(tmp_paths: Paths):
    """orchestrator-style: one teams.yaml entry, multiple projects;
    capsule renders one section per project."""
    _seed_project_file(tmp_paths, "alpha", learnings="alpha body")
    _seed_project_file(tmp_paths, "beta", memory="beta body")
    _seed_project_file(tmp_paths, "gamma", learnings="gamma body")
    _write_teams_yaml(tmp_paths, """
agents:
  orchestrator:
    projects: [alpha, beta, gamma]
""")
    out = ctx._render_project_capsule(tmp_paths, "@orchestrator")
    assert "## Project: alpha" in out
    assert "## Project: beta" in out
    assert "## Project: gamma" in out
    assert "alpha body" in out
    assert "beta body" in out
    assert "gamma body" in out


def test_resolution_teams_yaml_unregistered_project_skipped(
    tmp_paths: Paths,
):
    """teams.yaml lists a project whose dir doesn't exist → skipped
    (defensive degrade)."""
    _seed_project_file(tmp_paths, "alpha", learnings="alpha body")
    _write_teams_yaml(tmp_paths, """
agents:
  multi-probe:
    projects: [alpha, ghost-project]
""")
    out = ctx._render_project_capsule(tmp_paths, "@multi-probe")
    assert "## Project: alpha" in out
    assert "## Project: ghost-project" not in out


# ===========================================================================
# Precedence
# ===========================================================================


def test_resolution_frontmatter_overrides_teams_yaml(tmp_paths: Paths):
    """Frontmatter wins. teams.yaml entry exists but the agent's
    MISSION.md declares a different project."""
    _seed_root_agent_mission(
        tmp_paths, "@override-probe",
        frontmatter="project: writing\n",
    )
    _seed_project_file(tmp_paths, "writing", learnings="frontmatter-wins")
    _seed_project_file(tmp_paths, "widget", learnings="teams-loses")
    _write_teams_yaml(tmp_paths, """
agents:
  override-probe:
    projects: [widget]
""")
    out = ctx._render_project_capsule(tmp_paths, "@override-probe")
    assert "## Project: writing" in out
    assert "frontmatter-wins" in out
    assert "## Project: widget" not in out
    assert "teams-loses" not in out


def test_resolution_teams_yaml_overrides_path_inference(tmp_paths: Paths):
    """Project-nested agent (path → project A) with teams.yaml entry
    pointing to project B → teams.yaml wins."""
    _seed_project_nested_agent_mission(
        tmp_paths, "widget", "@nested-probe",
    )
    _seed_project_file(
        tmp_paths, "widget", learnings="path-inference-result",
    )
    _seed_project_file(
        tmp_paths, "writing", learnings="teams-yaml-result",
    )
    _write_teams_yaml(tmp_paths, """
agents:
  nested-probe:
    projects: [writing]
""")
    out = ctx._render_project_capsule(tmp_paths, "@nested-probe")
    assert "## Project: writing" in out
    assert "teams-yaml-result" in out
    assert "## Project: widget" not in out
    assert "path-inference-result" not in out


# ===========================================================================
# Fallthrough behavior
# ===========================================================================


def test_resolution_path_nested_fallback_when_teams_empty(tmp_paths: Paths):
    """Project-nested agent with NO teams.yaml entry → path
    inference still works (B4 path branch preserved)."""
    _seed_project_nested_agent_mission(
        tmp_paths, "widget", "@b4-fallback",
    )
    _seed_project_file(
        tmp_paths, "widget", learnings="path-fallback-result",
    )
    # teams.yaml exists but doesn't list this agent.
    _write_teams_yaml(tmp_paths, """
agents:
  somebody-else:
    projects: [writing]
""")
    out = ctx._render_project_capsule(tmp_paths, "@b4-fallback")
    assert "## Project: widget" in out
    assert "path-fallback-result" in out


def test_resolution_b7_name_prefix_inference_REMOVED(tmp_paths: Paths):
    """Critical regression: ``@widget-eng-fresh`` at root scope
    with project ``widget`` registered and NO teams.yaml entry +
    NO frontmatter must NOT auto-resolve. Name-prefix string-match
    is gone."""
    _seed_root_agent_mission(tmp_paths, "@widget-eng-fresh")
    _seed_project_file(
        tmp_paths, "widget", learnings="must-not-render",
    )
    # teams.yaml exists but doesn't cover this agent.
    _write_teams_yaml(tmp_paths, """
agents:
  somebody-else:
    projects: [writing]
""")
    out = ctx._render_project_capsule(tmp_paths, "@widget-eng-fresh")
    assert out == ""


def test_resolution_polymarket_agents_brittleness_was_real(
    tmp_paths: Paths,
):
    """Critic-flagged B4 case: ``@polymarket-agents-research`` would
    first-dash-split to ``polymarket``, which doesn't match project
    ``polymarket-agents``. teams.yaml resolves it cleanly. With
    name-prefix removed, the only way this works is via teams.yaml
    or frontmatter."""
    _seed_root_agent_mission(tmp_paths, "@polymarket-agents-research")
    _seed_project_file(
        tmp_paths, "polymarket-agents",
        learnings="canonical polymarket-agents body",
    )
    _write_teams_yaml(tmp_paths, """
agents:
  polymarket-agents-research:
    projects: [polymarket-agents]
""")
    out = ctx._render_project_capsule(
        tmp_paths, "@polymarket-agents-research",
    )
    assert "## Project: polymarket-agents" in out
    assert "canonical polymarket-agents body" in out


def test_resolution_no_mission_no_teams_no_path_returns_empty(
    tmp_paths: Paths,
):
    """Graceful no-op when EVERY resolution step misses."""
    out = ctx._render_project_capsule(tmp_paths, "@orphan-agent")
    assert out == ""


def test_resolution_no_teams_yaml_file_falls_through(tmp_paths: Paths):
    """No teams.yaml at all → resolution falls through to path
    inference cleanly without crashing."""
    _seed_project_nested_agent_mission(
        tmp_paths, "widget", "@nested-no-teams",
    )
    _seed_project_file(
        tmp_paths, "widget", learnings="path-only-result",
    )
    # No teams.yaml seeded.
    _teams._CACHE.clear()
    out = ctx._render_project_capsule(tmp_paths, "@nested-no-teams")
    assert "## Project: widget" in out
    assert "path-only-result" in out
