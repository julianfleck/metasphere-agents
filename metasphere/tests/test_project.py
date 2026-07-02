"""Tests for ``metasphere.project`` — foundational project API.

Layer-A coverage: ``init_project`` idempotency + marker creation,
``list_projects`` registration, ``_validate_name`` rules, changelog +
learnings file generation, and the ``project.json`` seed flow.
Sibling layers (members, schema, telegram, context, lifecycle) live in
their own ``test_project_*`` files.
"""

from pathlib import Path

import pytest

from metasphere.project import (
    _validate_name,
    init_project,
    list_projects,
    new_project,
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
    # Canonical-layout scaffold: .tasks/.messages/.changelog/.learnings/shared
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


def test_validate_name_rejects_leading_dash():
    """``--help`` is the canonical failure: a CLI flag that leaked into
    a positional argument and ended up registered as a project name
    (real incident, see registry entry path ``~/.metasphere/--help``).
    Anything starting with ``-`` is almost certainly an argv leak.
    """
    with pytest.raises(ValueError, match="CLI flag"):
        _validate_name("--help")
    with pytest.raises(ValueError, match="CLI flag"):
        _validate_name("-h")
    with pytest.raises(ValueError, match="CLI flag"):
        _validate_name("--path")


def test_validate_name_rejects_empty_and_path_separators():
    with pytest.raises(ValueError, match="non-empty"):
        _validate_name("")
    with pytest.raises(ValueError, match="invalid"):
        _validate_name("a/b")
    with pytest.raises(ValueError, match="invalid"):
        _validate_name("a\\b")


def test_validate_name_accepts_normal_names():
    # No raise.
    _validate_name("widget")
    _validate_name("ww-eng")
    _validate_name("rage_2026")
    _validate_name("a.b")


def test_init_project_rejects_flag_like_path(tmp_paths, tmp_path, monkeypatch):
    """When called with ``path=Path('--help')``, the derived name would
    have been ``--help`` (a real registry-pollution incident). Validation
    must catch this before ``_register`` writes ``projects.json``.
    """
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="CLI flag"):
        init_project(path=Path("--help"), paths=tmp_paths)


def test_new_project_rejects_flag_like_name(tmp_paths):
    with pytest.raises(ValueError, match="CLI flag"):
        new_project("--help", paths=tmp_paths)




def test_list_projects_distinguishes_rows_sharing_a_path(tmp_paths):
    """Two registry rows may legitimately share a repo path (an overlay
    project sharing a repo with another). Each must load by its OWN name →
    canonical home, not collapse to whichever row registered the path
    first — that mislabeled the later row as a duplicate of the first and
    silently hid a real project (with its own agents) from the landscape.
    """
    from metasphere.io import write_json
    from metasphere.project import _canonical_project_file, _projects_file

    shared = "/repo/shared"
    for n in ("writer", "research"):
        cf = _canonical_project_file(n, tmp_paths)
        cf.parent.mkdir(parents=True, exist_ok=True)
        write_json(cf, {"name": n, "path": shared, "status": "active"})
    write_json(_projects_file(tmp_paths), [
        {"name": "writer", "path": shared, "registered": "2026-01-01T00:00:00Z"},
        {"name": "research", "path": shared, "registered": "2026-01-02T00:00:00Z"},
    ])

    names = [p.name for p in list_projects(paths=tmp_paths)]
    assert names.count("writer") == 1
    assert names.count("research") == 1  # the hidden row, recovered
