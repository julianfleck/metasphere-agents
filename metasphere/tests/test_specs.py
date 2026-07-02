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


def test_substitute_does_not_rho_on_value():
    """A substituted value containing ``{{...}}`` syntax stays literal.

    If a variable's value happens to contain its own ``{{...}}``
    placeholder syntax (e.g. an agent's USER.md mentions an unfilled
    template), the substitution must NOT rho and try to expand the
    inner placeholder. Single-pass replacement is the contract.
    """
    out = _specs._substitute(
        "Project: {{name}}",
        {"name": "{{ project_name }} (literal)"},
    )
    # Inner ``{{ project_name }}`` stays literal — no recursive expansion.
    assert out == "Project: {{ project_name }} (literal)"

    # And a second pass through _substitute with project_name set
    # WOULD expand it. Single-pass-only is intentional: callers run
    # _substitute once per template and the values are trusted as
    # literals from that point on.
    out2 = _specs._substitute(out, {"project_name": "alpha"})
    assert out2 == "Project: alpha (literal)"


# ---------- _seed_project_user_md ----------

def test_seed_project_user_md_creates_file_from_template(tmp_paths):
    """Project-level USER.md is seeded from the shipped template."""
    _register_project(tmp_paths, "alpha")
    user_md = _specs._seed_project_user_md("alpha", "build something", tmp_paths)
    assert user_md is not None
    assert user_md.is_file()
    content = user_md.read_text(encoding="utf-8")
    assert "alpha" in content
    assert "build something" in content


def test_seed_project_user_md_idempotent(tmp_paths):
    """Re-seeding does not overwrite an existing project USER.md."""
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
    _register_project(tmp_paths, "alpha")
    spec = _seed_test_spec(tmp_paths.project_root / "templates" / "agents" / "researcher",
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
    spec = _seed_test_spec(tmp_paths.project_root / "templates" / "agents" / "researcher",
                           name="researcher", role="researcher")
    agent_dir = _specs.seed_agent(
        "@root-research", spec, paths=tmp_paths,
    )
    # No project-scope -> no USER.md should be created here
    assert not (agent_dir / "USER.md").exists()


def test_seed_agent_two_agents_share_one_project_user_md(tmp_paths):
    """Two agents on the same project share the same USER.md target."""
    _register_project(tmp_paths, "alpha")
    spec = _seed_test_spec(tmp_paths.project_root / "templates" / "agents" / "researcher",
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
    spec = _seed_test_spec(tmp_paths.project_root / "templates" / "agents" / "researcher",
                           name="researcher", role="researcher")
    agent_dir = _specs.seed_agent(
        "@alpha-research", spec,
        project_name="alpha", project_goal="goal", paths=tmp_paths,
    )
    assert not (agent_dir / "USER.md").exists()
    assert not (tmp_paths.root / "projects" / "alpha" / "USER.md").exists()


def test_seed_agent_preserves_existing_agent_user_md(tmp_paths):
    """Operator-customized agent USER.md is not clobbered by re-seeding."""
    _register_project(tmp_paths, "alpha")
    spec = _seed_test_spec(tmp_paths.project_root / "templates" / "agents" / "researcher",
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


# ---------- seed_agent name validation (flag-leak guard) ----------

@pytest.mark.parametrize(
    "bad_name",
    [
        "--bogus",
        "-h",
        "@--help",
        "@-typo",
        "",
        "   ",
        "with/slash",
        "with\\backslash",
    ],
)
def test_seed_agent_rejects_invalid_names(tmp_paths, bad_name):
    spec = _seed_test_spec(
        tmp_paths.project_root / "templates" / "agents" / "researcher",
        name="researcher", role="researcher",
    )
    with pytest.raises(ValueError):
        _specs.seed_agent(bad_name, spec, paths=tmp_paths)
    # No ghost agent dir for the bad name (either raw or @-prefixed).
    assert not (tmp_paths.agents / bad_name).exists()
    assert not (tmp_paths.agents / f"@{bad_name}").exists()


# ---------- legacy spec-name resolution ----------

# ---------- shipped-template metadata uses substitution, not literal names ----------

# After PR #140 collapsed specs/ into templates/agents/<role>/, the
# config.md ``name`` field was renamed (implementer->eng, planner->lead,
# reviewer->critic, monitor->explorer) but each role's MISSION.md and
# SOUL.md still carried the OLD ``Role: <old>`` / ``Spec: <old>`` literal
# strings. Result: a fresh ``--spec eng`` seed would land MISSION.md
# reading ``Role: developer / Spec: implementer`` — pointing at names
# that no longer exist anywhere else in the codebase.
#
# Fix is to make the metadata reference template variables (``{{role}}``
# and ``{{spec_name}}``) so a future rename only needs to touch config.md.
# This test pins that contract: every shipped role's MISSION/SOUL uses
# substitution, not hardcoded names.

@pytest.mark.parametrize("role", ["eng", "lead", "critic", "explorer", "researcher"])
def test_shipped_mission_uses_role_substitution(role):
    pkg_root = Path(_specs.__file__).resolve().parent.parent
    mission = pkg_root / "templates" / "agents" / role / "MISSION.md"
    text = mission.read_text(encoding="utf-8")
    assert "Role: {{role}}" in text, (
        f"templates/agents/{role}/MISSION.md must use Role: {{{{role}}}} "
        f"substitution so a future rename touches only config.md"
    )
    assert "Spec: {{spec_name}}" in text, (
        f"templates/agents/{role}/MISSION.md must use Spec: {{{{spec_name}}}}"
    )


@pytest.mark.parametrize("role", ["eng", "lead", "critic", "explorer", "researcher"])
def test_shipped_soul_uses_role_substitution(role):
    pkg_root = Path(_specs.__file__).resolve().parent.parent
    soul = pkg_root / "templates" / "agents" / role / "SOUL.md"
    text = soul.read_text(encoding="utf-8")
    assert "Role: {{role}}" in text, (
        f"templates/agents/{role}/SOUL.md must use Role: {{{{role}}}} "
        f"substitution so a future rename touches only config.md"
    )


@pytest.mark.parametrize("role", ["eng", "lead", "critic", "explorer", "researcher"])
def test_seed_agent_renders_role_metadata_matching_config(tmp_paths, role):
    """End-to-end: seeding from a shipped role template lands MISSION.md
    with ``Role: <role>`` and ``Spec: <role>`` — never a stale legacy
    name. Catches a re-introduction of the PR #140 collapse drift."""
    pkg_root = Path(_specs.__file__).resolve().parent.parent
    spec = _specs.get_spec(role, paths=tmp_paths)
    assert spec is not None, f"shipped spec {role!r} should resolve via get_spec"
    # Skip if shipped spec dir differs from package-relative path (no-op
    # in CI; the get_spec lookup tier-walk handles this).
    assert spec.spec_dir == pkg_root / "templates" / "agents" / role
    agent_dir = _specs.seed_agent(f"@{role}-test", spec, paths=tmp_paths)
    mission = (agent_dir / "MISSION.md").read_text(encoding="utf-8")
    soul = (agent_dir / "SOUL.md").read_text(encoding="utf-8")
    assert f"Role: {role}" in mission
    assert f"Spec: {role}" in mission
    assert f"Role: {role}" in soul


# --- surfaces frontmatter parsing (PR multi-surface-routing) ---------------


def _write_spec(tmp_paths, name: str, body: str):
    spec_dir = tmp_paths.root / "templates" / "agents" / name
    spec_dir.mkdir(parents=True, exist_ok=True)
    (spec_dir / "config.md").write_text(body, encoding="utf-8")
    return spec_dir


def test_agent_spec_parses_surfaces_list(tmp_paths):
    """``surfaces: [a, b]`` frontmatter parses into a typed list."""
    spec_dir = _write_spec(
        tmp_paths, "fanout",
        "---\n"
        "name: fanout\n"
        "role: eng\n"
        "surfaces: [telegram-relay, slack-relay]\n"
        "---\n",
    )
    spec = _specs.AgentSpec.from_dir(spec_dir)
    assert spec is not None
    assert spec.surfaces == ["telegram-relay", "slack-relay"]


def test_agent_spec_missing_surfaces_defaults_empty(tmp_paths):
    """A spec without ``surfaces`` gets an empty list (legacy back-compat)."""
    spec_dir = _write_spec(
        tmp_paths, "quiet",
        "---\n"
        "name: quiet\n"
        "role: eng\n"
        "---\n",
    )
    spec = _specs.AgentSpec.from_dir(spec_dir)
    assert spec is not None
    assert spec.surfaces == []


@pytest.mark.parametrize(
    "legacy,new",
    [
        ("implementer", "eng"),
        ("planner", "lead"),
        ("reviewer", "critic"),
        ("monitor", "explorer"),
    ],
)
def test_get_spec_legacy_name_returns_none_and_warns(
    tmp_paths, caplog, legacy, new,
):
    """Legacy spec names (pre-collapse) resolve to None but the warning
    names the new spec so operators can fix shell aliases / scripts
    without us preserving the alias map as a live code path."""
    import logging
    with caplog.at_level(logging.WARNING, logger="metasphere.specs"):
        result = _specs.get_spec(legacy, paths=tmp_paths)
    assert result is None
    rendered = " ".join(r.getMessage() for r in caplog.records)
    assert f"'{legacy}'" in rendered
    assert f"'{new}'" in rendered
    assert f"--spec {new}" in rendered
