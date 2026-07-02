"""Tests for the per-project memory migration cold-start nudge.

Phase 1 of the per-project memory + learnings architectural change
(spec at ``~/.metasphere/agents/@orchestrator/artifacts/2026-05-29-
per-project-memory-learnings-spec.md``, section "Periodic check in
the hook"). T2 ships the residual nudge only — auto-migration is
intentionally out of scope.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from metasphere import context as ctx
from metasphere.paths import Paths


def _seed_agent(tmp_paths: Paths, agent: str, *,
                learnings: str = "", memory: str = "") -> Path:
    d = tmp_paths.agent_dir(agent)
    d.mkdir(parents=True, exist_ok=True)
    if learnings:
        (d / "LEARNINGS.md").write_text(learnings, encoding="utf-8")
    if memory:
        (d / "MEMORY.md").write_text(memory, encoding="utf-8")
    return d


def _register_project(tmp_paths: Paths, name: str) -> Path:
    pdir = tmp_paths.projects / name
    pdir.mkdir(parents=True, exist_ok=True)
    return pdir


# ---------------------------------------------------------------------------
# No-op cases.
# ---------------------------------------------------------------------------


def test_no_agent_files_returns_empty(tmp_paths: Paths):
    # Agent dir exists but no LEARNINGS / MEMORY.
    d = tmp_paths.agent_dir("@bare")
    d.mkdir(parents=True, exist_ok=True)
    _register_project(tmp_paths, "widget")
    assert ctx._render_project_migration_nudge(tmp_paths, "@bare") == ""


def test_no_registered_projects_returns_empty(tmp_paths: Paths):
    _seed_agent(tmp_paths, "@alpha", learnings="some content")
    # Wipe the auto-seeded testproj dir so token enumeration finds none.
    import shutil
    if (tmp_paths.projects / "testproj").is_dir():
        shutil.rmtree(tmp_paths.projects / "testproj")
    assert ctx._render_project_migration_nudge(tmp_paths, "@alpha") == ""


def test_no_matching_tokens_returns_empty_and_writes_sentinel(tmp_paths: Paths):
    _register_project(tmp_paths, "widget")
    d = _seed_agent(
        tmp_paths, "@alpha",
        learnings="generic coordination habits, no project tokens here.",
    )
    out = ctx._render_project_migration_nudge(tmp_paths, "@alpha")
    assert out == ""
    # Sentinel still written so next turn doesn't re-scan.
    assert (d / "state" / "migration_nudge_seen").is_file()


# ---------------------------------------------------------------------------
# Detection.
# ---------------------------------------------------------------------------


def test_matches_in_learnings_emits_nudge(tmp_paths: Paths):
    _register_project(tmp_paths, "widget")
    _register_project(tmp_paths, "rho")
    _seed_agent(
        tmp_paths, "@alpha",
        learnings=(
            "- widget VACUUM lessons at 20M chunks.\n"
            "- generic coordination habit unrelated.\n"
            "- rho sense-making substrate.\n"
        ),
    )
    out = ctx._render_project_migration_nudge(tmp_paths, "@alpha")
    assert "## Per-project memory migration" in out
    assert "look project-specific" in out
    assert "widget" in out
    assert "rho" in out
    # Total reference count surfaced.
    assert "2 references" in out


def test_single_hit_uses_singular_noun(tmp_paths: Paths):
    # N=1 must read "1 reference" — not "1 references".
    _register_project(tmp_paths, "widget")
    _seed_agent(
        tmp_paths, "@alpha",
        learnings="- widget VACUUM lessons at 20M chunks.\n",
    )
    out = ctx._render_project_migration_nudge(tmp_paths, "@alpha")
    assert "1 reference" in out
    assert "1 references" not in out


def test_word_boundary_avoids_spurious_substring(tmp_paths: Paths):
    # Project named "wire" must NOT match "widget" or "wireless".
    _register_project(tmp_paths, "wire")
    _seed_agent(
        tmp_paths, "@alpha",
        learnings=(
            "- widget VACUUM lessons.\n"
            "- wireless protocol notes.\n"
        ),
    )
    out = ctx._render_project_migration_nudge(tmp_paths, "@alpha")
    assert out == ""


def test_case_insensitive_match(tmp_paths: Paths):
    _register_project(tmp_paths, "widget")
    _seed_agent(
        tmp_paths, "@alpha",
        learnings="- Widget pipeline architecture.\n",
    )
    out = ctx._render_project_migration_nudge(tmp_paths, "@alpha")
    assert "## Per-project memory migration" in out
    assert "widget" in out


def test_match_in_memory_file_also_counts(tmp_paths: Paths):
    _register_project(tmp_paths, "widget")
    _seed_agent(
        tmp_paths, "@alpha",
        memory="- widget monitor reads public.articles directly.\n",
    )
    out = ctx._render_project_migration_nudge(tmp_paths, "@alpha")
    assert "widget" in out


# ---------------------------------------------------------------------------
# Sentinel caching.
# ---------------------------------------------------------------------------


def test_repeat_invocation_unchanged_files_suppressed(tmp_paths: Paths):
    _register_project(tmp_paths, "widget")
    _seed_agent(
        tmp_paths, "@alpha",
        learnings="- widget VACUUM lessons.\n",
    )
    first = ctx._render_project_migration_nudge(tmp_paths, "@alpha")
    assert first != ""
    second = ctx._render_project_migration_nudge(tmp_paths, "@alpha")
    assert second == "", "sentinel should suppress repeat surfacing"


def test_file_mtime_change_reemits_nudge(tmp_paths: Paths):
    _register_project(tmp_paths, "widget")
    d = _seed_agent(
        tmp_paths, "@alpha",
        learnings="- widget VACUUM lessons.\n",
    )
    first = ctx._render_project_migration_nudge(tmp_paths, "@alpha")
    assert first != ""
    # Bump mtime by rewriting. st_mtime_ns guarantees the change is
    # visible even on filesystems where st_mtime resolution is coarse.
    learnings_path = d / "LEARNINGS.md"
    sentinel = d / "state" / "migration_nudge_seen"
    fp_before = sentinel.read_text(encoding="utf-8")
    # Use os.utime with explicit ns to dodge sub-second resolution gaps.
    pre_ns = learnings_path.stat().st_mtime_ns
    os.utime(
        learnings_path,
        ns=(pre_ns + 1_000_000_000, pre_ns + 1_000_000_000),
    )
    second = ctx._render_project_migration_nudge(tmp_paths, "@alpha")
    assert second != "", "mtime bump should re-emit the nudge"
    fp_after = sentinel.read_text(encoding="utf-8")
    assert fp_after != fp_before


# ---------------------------------------------------------------------------
# End-to-end via build_context.
# ---------------------------------------------------------------------------


def test_build_context_emits_nudge_section(tmp_paths: Paths):
    _register_project(tmp_paths, "widget")
    _seed_agent(
        tmp_paths, "@e2e",
        learnings="- widget monitoring topology.\n",
    )
    os.environ["METASPHERE_AGENT_ID"] = "@e2e"
    try:
        out = ctx.build_context(tmp_paths)
        assert "## Per-project memory migration" in out
        # And it sits AFTER the project capsule slot (the per-project
        # capsule for @e2e is empty because no MISSION.md frontmatter
        # declares a project — so the capsule emits nothing, but the
        # ordering wire is verified by the section appearing at all).
        idx_mission = out.find("## Mission")
        idx_nudge = out.find("## Per-project memory migration")
        if idx_mission >= 0:
            assert idx_nudge > idx_mission
    finally:
        os.environ.pop("METASPHERE_AGENT_ID", None)
