"""Tests for metasphere.specs (agent persona seeding + USER.md bootstrap)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from metasphere import specs as _specs
from metasphere.specs import AgentSpec


def _seed_test_spec(spec_dir: Path, *, name: str, role: str) -> AgentSpec:
    """Create a minimal spec dir on disk and return the AgentSpec."""
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "config.md").write_text(
        f"---\nname: {name}\nrole: {role}\ndescription: test\n"
        f"sandbox: scoped\npersistent: true\n---\n"
    )
    (spec_dir / "SOUL.md").write_text(f"# {{{{agent_id}}}}\nrole: {role}\n")
    (spec_dir / "MISSION.md").write_text(
        "# Mission: {{agent_id}}\n\nProject: **{{project_name}}**\n{{project_goal}}\n"
    )
    return AgentSpec(
        name=name, role=role, description="test",
        sandbox="scoped", persistent=True, spec_dir=spec_dir,
    )


def _register_project(tmp_paths, name: str) -> Path:
    """Register a project so resolve_agent_dir routes correctly."""
    import json
    proj_dir = tmp_paths.root / "projects" / name
    proj_dir.mkdir(parents=True, exist_ok=True)
    (proj_dir / "project.json").write_text(json.dumps({
        "schema": 2, "name": name, "path": str(proj_dir),
        "created": "2026-04-30T00:00:00Z", "status": "active",
    }))
    registry = tmp_paths.root / "projects.json"
    existing = []
    if registry.is_file():
        existing = json.loads(registry.read_text())
    existing.append({
        "name": name, "path": str(proj_dir),
        "registered": "2026-04-30T00:00:00Z",
    })
    registry.write_text(json.dumps(existing))
    return proj_dir


def _shipped_user_md_template_path() -> Path:
    return (Path(__file__).resolve().parent.parent.parent
            / "templates" / "install" / "projects" / "USER.md.template")


# ---------- _substitute ----------

def test_substitute_no_space_form():
    out = _specs._substitute("Hi {{name}}!", {"name": "Eng"})
    assert out == "Hi Eng!"


def test_substitute_spaced_form():
    out = _specs._substitute("Hi {{ name }}!", {"name": "Eng"})
    assert out == "Hi Eng!"


def test_substitute_unknown_key_left_intact():
    out = _specs._substitute("Hi {{unknown}}!", {"name": "Eng"})
    assert out == "Hi {{unknown}}!"


def test_substitute_multiple_keys_mixed_styles():
    out = _specs._substitute(
        "{{a}} and {{ b }} but not {{ c }}",
        {"a": "A", "b": "B"},
    )
    assert out == "A and B but not {{ c }}"


# ---------- _seed_project_user_md ----------

def test_seed_project_user_md_creates_file_from_template(tmp_paths):
    """Project-level USER.md is seeded from the shipped template."""
    if not _shipped_user_md_template_path().is_file():
        pytest.skip("USER.md.template not yet shipped")
    _register_project(tmp_paths, "alpha")
    user_md = _specs._seed_project_user_md("alpha", "build something", tmp_paths)
    assert user_md is not None
    assert user_md.is_file()
    content = user_md.read_text(encoding="utf-8")
    assert "alpha" in content
    assert "build something" in content


def test_seed_project_user_md_idempotent(tmp_paths):
    """Re-seeding does not overwrite an existing project USER.md."""
    if not _shipped_user_md_template_path().is_file():
        pytest.skip("USER.md.template not yet shipped")
    _register_project(tmp_paths, "alpha")
    user_md = _specs._seed_project_user_md("alpha", "v1", tmp_paths)
    assert user_md is not None
    user_md.write_text("CUSTOMIZED\n")
    again = _specs._seed_project_user_md("alpha", "v2", tmp_paths)
    assert again == user_md
    assert user_md.read_text() == "CUSTOMIZED\n"


# ---------- seed_agent USER.md wiring ----------

def test_seed_agent_links_user_md_for_project_scoped(tmp_paths):
    """Project-scoped agent gets a symlink USER.md -> project's USER.md."""
    if not _shipped_user_md_template_path().is_file():
        pytest.skip("USER.md.template not yet shipped")
    _register_project(tmp_paths, "alpha")
    spec = _seed_test_spec(tmp_paths.project_root / "specs" / "researcher",
                           name="researcher", role="researcher")
    agent_dir = _specs.seed_agent(
        "@alpha-research", spec,
        project_name="alpha", project_goal="goal", paths=tmp_paths,
    )
    user_md = agent_dir / "USER.md"
    assert user_md.is_symlink()
    target = os.readlink(user_md)
    # Resolves to project's USER.md
    project_user = (tmp_paths.root / "projects" / "alpha" / "USER.md").resolve()
    assert (agent_dir / target).resolve() == project_user
    # Content reaches the agent through the symlink
    text = user_md.read_text(encoding="utf-8")
    assert "alpha" in text
    assert "goal" in text


def test_seed_agent_skips_user_md_for_root_scoped(tmp_paths):
    """Root-scoped agent (no project_name) does not get USER.md handling."""
    spec = _seed_test_spec(tmp_paths.project_root / "specs" / "researcher",
                           name="researcher", role="researcher")
    agent_dir = _specs.seed_agent(
        "@root-research", spec, paths=tmp_paths,
    )
    # No project-scope -> no USER.md should be created here
    assert not (agent_dir / "USER.md").exists()


def test_seed_agent_two_agents_share_one_project_user_md(tmp_paths):
    """Two agents on the same project share the same USER.md target."""
    if not _shipped_user_md_template_path().is_file():
        pytest.skip("USER.md.template not yet shipped")
    _register_project(tmp_paths, "alpha")
    spec = _seed_test_spec(tmp_paths.project_root / "specs" / "researcher",
                           name="researcher", role="researcher")
    a = _specs.seed_agent(
        "@alpha-research", spec, project_name="alpha", project_goal="g1",
        paths=tmp_paths,
    )
    b = _specs.seed_agent(
        "@alpha-eng", spec, project_name="alpha", project_goal="ignored",
        paths=tmp_paths,
    )
    project_user = tmp_paths.root / "projects" / "alpha" / "USER.md"
    # Project USER.md retains the FIRST goal (idempotent seed)
    assert "g1" in project_user.read_text()
    # Both agents' symlinks resolve to the same project file
    assert (a / "USER.md").resolve() == project_user.resolve()
    assert (b / "USER.md").resolve() == project_user.resolve()


def test_seed_agent_user_md_no_template_leaves_unset(tmp_paths, monkeypatch):
    """If the shipped template is unavailable, USER.md handling no-ops."""
    monkeypatch.setattr(_specs, "_find_user_md_template", lambda: None)
    _register_project(tmp_paths, "alpha")
    spec = _seed_test_spec(tmp_paths.project_root / "specs" / "researcher",
                           name="researcher", role="researcher")
    agent_dir = _specs.seed_agent(
        "@alpha-research", spec,
        project_name="alpha", project_goal="goal", paths=tmp_paths,
    )
    assert not (agent_dir / "USER.md").exists()
    assert not (tmp_paths.root / "projects" / "alpha" / "USER.md").exists()


def test_seed_agent_preserves_existing_agent_user_md(tmp_paths):
    """Operator-customized agent USER.md is not clobbered by re-seeding."""
    if not _shipped_user_md_template_path().is_file():
        pytest.skip("USER.md.template not yet shipped")
    _register_project(tmp_paths, "alpha")
    spec = _seed_test_spec(tmp_paths.project_root / "specs" / "researcher",
                           name="researcher", role="researcher")
    a = _specs.seed_agent(
        "@alpha-research", spec, project_name="alpha", project_goal="g",
        paths=tmp_paths,
    )
    # Replace symlink with operator-customized real file
    user_md = a / "USER.md"
    user_md.unlink()
    user_md.write_text("CUSTOMIZED LOCALLY\n")
    # Re-seed: should NOT overwrite
    _specs.seed_agent(
        "@alpha-research", spec, project_name="alpha", project_goal="g",
        paths=tmp_paths,
    )
    assert user_md.read_text() == "CUSTOMIZED LOCALLY\n"
    assert not user_md.is_symlink()
