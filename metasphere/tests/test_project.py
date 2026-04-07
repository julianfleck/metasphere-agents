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
    assert (proj_dir / ".metasphere" / "project.json").exists()
    assert (proj_dir / ".tasks" / "active").is_dir()
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
    # Bash bug fix: file actually exists with content
    assert out.exists()
    text = out.read_text()
    assert "Changelog" in text
    assert "proj" in text


def test_changelog_walks_completed_tasks(tmp_paths, tmp_path):
    """M3 (wave-4 review): completed tasks come from .tasks/completed/*.task on disk."""
    proj = tmp_path / "proj2"
    proj.mkdir()
    init_project(path=proj, paths=tmp_paths)
    completed = proj / ".tasks" / "completed"
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
