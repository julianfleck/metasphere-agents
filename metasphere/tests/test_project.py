from pathlib import Path

from metasphere.project import (
    init_project,
    list_projects,
    project_changelog,
    project_learnings,
)


def test_init_creates_marker_and_registers(tmp_paths, tmp_path):
    proj_dir = tmp_path / "alpha"
    proj_dir.mkdir()
    p = init_project(path=proj_dir, paths=tmp_paths)
    # In-repo legacy marker + canonical project.json both still created.
    assert (proj_dir / ".metasphere").is_dir()
    assert (tmp_paths.projects / "alpha" / "project.json").exists()
    # Canonical-layout scaffold: .tasks/.messages/.changelog/.learnings
    # now live under ~/.metasphere/projects/<name>/, not in-repo.
    assert (tmp_paths.projects / "alpha" / ".tasks" / "active").is_dir()
    assert (tmp_paths.projects / "alpha" / ".messages" / "inbox").is_dir()
    rows = list_projects(paths=tmp_paths)
    assert any(r.path == str(proj_dir.resolve()) for r in rows)
    assert p.name == "alpha"


def test_init_idempotent(tmp_paths, tmp_path):
    proj = tmp_path / "p"
    proj.mkdir()
    init_project(path=proj, paths=tmp_paths)
    init_project(path=proj, paths=tmp_paths)
    rows = list_projects(paths=tmp_paths)
    assert sum(1 for r in rows if r.path == str(proj.resolve())) == 1


def test_changelog_writes_file(tmp_paths, tmp_path):
    proj = tmp_path / "proj"
    proj.mkdir()
    init_project(path=proj, paths=tmp_paths)
    out = project_changelog("proj", paths=tmp_paths)
    # File actually exists with content
    assert out.exists()
    text = out.read_text()
    assert "Changelog" in text
    assert "proj" in text


def test_changelog_walks_completed_tasks(tmp_paths, tmp_path):
    """Completed tasks come from canonical .tasks/completed/*.task under
    ~/.metasphere/projects/<name>/ (not in-repo post-PR #10).
    """
    proj = tmp_path / "proj2"
    proj.mkdir()
    init_project(path=proj, paths=tmp_paths)
    completed = tmp_paths.projects / "proj2" / ".tasks" / "completed"
    completed.mkdir(parents=True, exist_ok=True)
    (completed / "task-1.task").write_text("title: Ship widget\nstatus: completed\n")
    (completed / "task-2.task").write_text("title: Refactor frobber\nstatus: completed\n")
    out = project_changelog("proj2", paths=tmp_paths)
    body = out.read_text()
    assert "Ship widget" in body
    assert "Refactor frobber" in body


def test_learnings_emits_agent_header_once(tmp_paths, tmp_path):
    proj = tmp_path / "lp"
    proj.mkdir()
    init_project(path=proj, paths=tmp_paths)

    agent_dir = tmp_paths.agents / "@worker"
    (agent_dir / "learnings").mkdir(parents=True)
    (agent_dir / "scope").write_text(str(proj.resolve()))
    (agent_dir / "learnings" / "first.md").write_text("learned A\n")
    (agent_dir / "learnings" / "second.md").write_text("learned B\n")

    out = project_learnings("lp", paths=tmp_paths)
    text = out.read_text()
    # Inverted-flag bug fix: header appears exactly once
    assert text.count("## @worker") == 1
    assert "### first" in text
    assert "### second" in text
    assert "learned A" in text and "learned B" in text


def test_changelog_missing_project(tmp_paths):
    import pytest
    with pytest.raises(FileNotFoundError):
        project_changelog("nope", paths=tmp_paths)


def test_init_seeds_project_claude_md_from_template(tmp_paths, tmp_path):
    """``init_project`` writes ~/.metasphere/projects/<name>/CLAUDE.md
    from the shipped template with project_name + goal_one_line
    substituted; other placeholders left for the operator to fill.
    """
    proj_dir = tmp_path / "alpha"
    proj_dir.mkdir()
    init_project(path=proj_dir, paths=tmp_paths,
                 goal="build something cool")
    claude_md = tmp_paths.projects / "alpha" / "CLAUDE.md"
    assert claude_md.is_file()
    text = claude_md.read_text()
    assert "alpha" in text  # project_name substituted
    assert "build something cool" in text  # goal_one_line substituted
    # Operator-fill placeholders left as-is (single-pass substitution
    # only fills the two known keys at init time).
    assert "{{ current_state_bullets }}" in text
    assert "{{ key_artifacts_paths }}" in text
    assert "{{ members_table }}" in text


def test_init_preserves_existing_project_claude_md(tmp_paths, tmp_path):
    """Re-init does not clobber an operator-customized CLAUDE.md."""
    proj_dir = tmp_path / "beta"
    proj_dir.mkdir()
    init_project(path=proj_dir, paths=tmp_paths, goal="v1")
    claude_md = tmp_paths.projects / "beta" / "CLAUDE.md"
    claude_md.write_text("OPERATOR-CUSTOMIZED\n")
    init_project(path=proj_dir, paths=tmp_paths, goal="v2")
    # The re-seed must not overwrite operator content.
    assert claude_md.read_text() == "OPERATOR-CUSTOMIZED\n"


