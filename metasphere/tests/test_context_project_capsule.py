"""Tests for ``_render_project_capsule`` — per-project LEARNINGS+MEMORY
injection from MISSION.md frontmatter.

Shape: shared ``~/.metasphere/projects/<P>/LEARNINGS.md`` + ``MEMORY.md``
read for each project declared by the agent. Multi-project agents
concat in declared order. Missing files skip silently.
"""

from __future__ import annotations

from pathlib import Path

from metasphere import context as ctx
from metasphere.paths import Paths


def _seed_agent_mission(tmp_paths: Paths, agent: str, frontmatter: str) -> Path:
    d = tmp_paths.agent_dir(agent)
    d.mkdir(parents=True, exist_ok=True)
    (d / "MISSION.md").write_text(
        "---\n" + frontmatter + "---\n\n# Mission\n\nbody\n",
        encoding="utf-8",
    )
    return d


def _seed_project_files(
    tmp_paths: Paths, project: str, *, learnings: str = "", memory: str = ""
) -> Path:
    pdir = tmp_paths.projects / project
    pdir.mkdir(parents=True, exist_ok=True)
    if learnings:
        (pdir / "LEARNINGS.md").write_text(learnings, encoding="utf-8")
    if memory:
        (pdir / "MEMORY.md").write_text(memory, encoding="utf-8")
    return pdir


def test_scalar_project_field_loads_files(tmp_paths: Paths):
    _seed_agent_mission(tmp_paths, "@alpha", "project: solo\n")
    _seed_project_files(
        tmp_paths, "solo",
        learnings="learned: VACUUM hurts at 20M rows.",
        memory="memory: a teammate owns Louvain inheritance.",
    )

    out = ctx._render_project_capsule(tmp_paths, "@alpha")

    assert "## Project: solo" in out
    assert "### LEARNINGS" in out
    assert "VACUUM hurts at 20M rows." in out
    assert "### MEMORY" in out
    assert "a teammate owns Louvain inheritance." in out


def test_list_projects_field_concats_in_order(tmp_paths: Paths):
    _seed_agent_mission(
        tmp_paths, "@beta",
        "projects: [rho, rho-server]\n",
    )
    _seed_project_files(
        tmp_paths, "rho", learnings="sense-making substrate.",
    )
    _seed_project_files(
        tmp_paths, "rho-server", memory="ops contact: alice.",
    )

    out = ctx._render_project_capsule(tmp_paths, "@beta")

    idx_rho = out.find("## Project: rho\n")
    idx_server = out.find("## Project: rho-server")
    assert idx_rho >= 0
    assert idx_server > idx_rho
    assert "sense-making substrate." in out
    assert "ops contact: alice." in out


def test_absent_field_returns_empty(tmp_paths: Paths):
    _seed_agent_mission(tmp_paths, "@gamma", "role: lead\n")
    _seed_project_files(
        tmp_paths, "solo", learnings="should not appear.",
    )
    assert ctx._render_project_capsule(tmp_paths, "@gamma") == ""


def test_project_listed_but_files_missing_skips_silently(tmp_paths: Paths):
    _seed_agent_mission(tmp_paths, "@delta", "project: ghost\n")
    # No project dir at all — must not crash.
    out = ctx._render_project_capsule(tmp_paths, "@delta")
    assert out == ""


def test_partial_project_files_partial_section(tmp_paths: Paths):
    _seed_agent_mission(tmp_paths, "@eps", "project: solo\n")
    _seed_project_files(
        tmp_paths, "solo", learnings="only learnings, no memory.",
    )
    out = ctx._render_project_capsule(tmp_paths, "@eps")
    assert "### LEARNINGS" in out
    assert "only learnings, no memory." in out
    assert "### MEMORY" not in out


def test_no_mission_file_returns_empty(tmp_paths: Paths):
    # Agent dir exists but no MISSION.md → nothing to parse.
    d = tmp_paths.agent_dir("@nomission")
    d.mkdir(parents=True, exist_ok=True)
    assert ctx._render_project_capsule(tmp_paths, "@nomission") == ""


def test_mixed_scalar_and_list_dedup_in_order(tmp_paths: Paths):
    _seed_agent_mission(
        tmp_paths, "@zeta",
        "project: solo\nprojects: [solo, other]\n",
    )
    _seed_project_files(
        tmp_paths, "solo", learnings="solo-learnings.",
    )
    _seed_project_files(
        tmp_paths, "other", learnings="other-learnings.",
    )
    out = ctx._render_project_capsule(tmp_paths, "@zeta")
    # solo appears once, before other.
    assert out.count("## Project: solo\n") == 1
    idx_solo = out.find("## Project: solo")
    idx_other = out.find("## Project: other")
    assert idx_solo >= 0
    assert idx_other > idx_solo


def test_per_file_byte_cap_enforced(tmp_paths: Paths):
    # Single file > cap is truncated by the capsule (not just the outer
    # ``truncate_section``) so no single project monopolises.
    _seed_agent_mission(tmp_paths, "@cap", "project: solo\n")
    big = "x" * (ctx._PROJECT_FILE_BYTE_CAP * 2)
    _seed_project_files(tmp_paths, "solo", learnings=big)
    out = ctx._render_project_capsule(tmp_paths, "@cap")
    # Bound: header + cap + trailing newline << 2x cap.
    assert len(out.encode("utf-8")) < ctx._PROJECT_FILE_BYTE_CAP * 2


def test_build_context_emits_project_section(tmp_paths: Paths):
    # End-to-end: build_context must pick up the capsule. Mirrors the
    # manual smoke transcript described in the T1 brief.
    import os
    os.environ["METASPHERE_AGENT_ID"] = "@e2e"
    try:
        _seed_agent_mission(tmp_paths, "@e2e", "project: solo\n")
        _seed_project_files(
            tmp_paths, "solo", learnings="sentinel-XYZ-12345",
        )
        out = ctx.build_context(tmp_paths)
        assert "sentinel-XYZ-12345" in out
        assert "## Project: solo" in out
    finally:
        os.environ.pop("METASPHERE_AGENT_ID", None)
