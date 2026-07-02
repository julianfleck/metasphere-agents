"""Tests for the project-capsule auto-inference fallback (B4).

When MISSION.md frontmatter declares no project, ``_render_project_capsule``
falls back to ``_infer_project_for_agent``: path layout first
(``projects/<P>/agents/@<agent>/``), then name prefix
(``@<project>-<role>`` + ``projects/<project>/`` exists).

Frontmatter remains the explicit override. Inference is single-project;
multi-project still requires ``projects: [a, b]`` in frontmatter.

Trigger: an existing agent's MISSION.md had no ``project:`` key, so
the T1 capsule no-op'd and the agent couldn't see its project's
LEARNINGS. Operator directive 2026-05-29: don't require frontmatter;
infer from layout.
"""

from __future__ import annotations

from pathlib import Path

from metasphere import context as ctx
from metasphere.paths import Paths


# ---------------------------------------------------------------------------
# Seed helpers — two layouts: root-scope agent dir vs project-nested.
# ---------------------------------------------------------------------------


def _seed_root_agent_mission(
    tmp_paths: Paths, agent: str, frontmatter: str = ""
) -> Path:
    """Agent at ``~/.metasphere/agents/@<id>/`` (no project nesting)."""
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
    """Agent at ``~/.metasphere/projects/<P>/agents/@<id>/``."""
    d = tmp_paths.project_agent_dir(project, agent)
    d.mkdir(parents=True, exist_ok=True)
    fm_block = ("---\n" + frontmatter + "---\n\n") if frontmatter else ""
    (d / "MISSION.md").write_text(
        fm_block + "# Mission\n\nbody\n", encoding="utf-8",
    )
    return d


def _seed_project_files(
    tmp_paths: Paths, project: str, *, learnings: str = "", memory: str = "",
) -> Path:
    pdir = tmp_paths.projects / project
    pdir.mkdir(parents=True, exist_ok=True)
    if learnings:
        (pdir / "LEARNINGS.md").write_text(learnings, encoding="utf-8")
    if memory:
        (pdir / "MEMORY.md").write_text(memory, encoding="utf-8")
    return pdir


# ---------------------------------------------------------------------------
# Probe 1: project-nested agent dir, NO frontmatter → path inference.
# ---------------------------------------------------------------------------


def test_path_inference_project_nested_agent(tmp_paths: Paths):
    _seed_project_nested_agent_mission(tmp_paths, "widget", "@probe1")
    _seed_project_files(
        tmp_paths, "widget",
        learnings="hetzner tunnel 670s pg idle timeout.",
    )

    out = ctx._render_project_capsule(tmp_paths, "@probe1")

    assert "## Project: widget" in out
    assert "hetzner tunnel 670s pg idle timeout." in out


# ---------------------------------------------------------------------------
# Root-scope agent, no frontmatter, no teams.yaml → no capsule.
# (B4's name-prefix string-match branch was removed in B7;
# ``@widget-eng`` at root scope without a teams.yaml entry no
# longer auto-resolves.)
# ---------------------------------------------------------------------------


def test_root_scope_no_inference_returns_empty(tmp_paths: Paths):
    _seed_root_agent_mission(tmp_paths, "@no-project-match")
    out = ctx._render_project_capsule(tmp_paths, "@no-project-match")
    assert out == ""


# ---------------------------------------------------------------------------
# Explicit frontmatter wins over path inference.
# ---------------------------------------------------------------------------


def test_frontmatter_overrides_path_inference(tmp_paths: Paths):
    # Agent dir says "widget" (path inference would pick it), but
    # frontmatter declares "customproject" — frontmatter wins.
    _seed_project_nested_agent_mission(
        tmp_paths, "widget", "@probe4",
        frontmatter="project: customproject\n",
    )
    _seed_project_files(
        tmp_paths, "widget", learnings="should-not-render",
    )
    _seed_project_files(
        tmp_paths, "customproject", learnings="this-must-render",
    )

    out = ctx._render_project_capsule(tmp_paths, "@probe4")

    assert "## Project: customproject" in out
    assert "this-must-render" in out
    assert "## Project: widget" not in out
    assert "should-not-render" not in out


# ---------------------------------------------------------------------------
# Multi-project frontmatter (regression on T1 list-projects path).
# ---------------------------------------------------------------------------


def test_multi_project_frontmatter_still_works(tmp_paths: Paths):
    _seed_root_agent_mission(
        tmp_paths, "@multi", frontmatter="projects: [a, b]\n",
    )
    _seed_project_files(tmp_paths, "a", learnings="a-learnings")
    _seed_project_files(tmp_paths, "b", memory="b-memory")

    out = ctx._render_project_capsule(tmp_paths, "@multi")

    assert "## Project: a" in out
    assert "a-learnings" in out
    assert "## Project: b" in out
    assert "b-memory" in out


# ---------------------------------------------------------------------------
# Helper-level unit coverage for _infer_project_for_agent.
# Path-nested branch only (name-prefix removed in B7).
# ---------------------------------------------------------------------------


def test_infer_returns_none_for_unprojected_root_agent(tmp_paths: Paths):
    agent_dir = _seed_root_agent_mission(tmp_paths, "@solo")
    assert ctx._infer_project_for_agent(
        tmp_paths, "@solo", agent_dir,
    ) is None


def test_infer_returns_project_for_nested_agent(tmp_paths: Paths):
    _seed_project_files(tmp_paths, "widget")
    agent_dir = _seed_project_nested_agent_mission(
        tmp_paths, "widget", "@inner",
    )
    assert ctx._infer_project_for_agent(
        tmp_paths, "@inner", agent_dir,
    ) == "widget"


def test_infer_no_name_prefix_resolution(tmp_paths: Paths):
    """B7 regression: ``@widget-probe`` at root scope with project
    ``widget`` registered must NOT resolve via the deleted
    name-prefix branch."""
    _seed_project_files(tmp_paths, "widget")
    agent_dir = _seed_root_agent_mission(tmp_paths, "@widget-probe")
    assert ctx._infer_project_for_agent(
        tmp_paths, "@widget-probe", agent_dir,
    ) is None
