"""project_for_scope + per-turn context-injection tests (layer D)."""

from pathlib import Path

from metasphere.context import _render_project
from metasphere.project import (
    add_member,
    new_project,
    project_for_scope,
)


def test_project_for_scope_finds_enclosing(tmp_paths, tmp_path):
    root = tmp_path / "proj"
    new_project("proj", path=root, paths=tmp_paths)
    nested = root / "a" / "b"
    nested.mkdir(parents=True)
    p = project_for_scope(nested, paths=tmp_paths)
    assert p is not None and p.name == "proj"


def test_project_for_scope_returns_none_outside(tmp_paths, tmp_path):
    assert project_for_scope(tmp_path, paths=tmp_paths) is None


def test_project_for_scope_at_root(tmp_paths, tmp_path):
    new_project("rootproj", path=tmp_path / "rp", paths=tmp_paths)
    p = project_for_scope(tmp_path / "rp", paths=tmp_paths)
    assert p is not None and p.name == "rootproj"


def test_render_project_empty_outside(tmp_paths, monkeypatch):
    # scope is tmp_paths.scope which is tmp_path/repo — not inside a project
    block = _render_project(tmp_paths)
    assert block == ""


def test_render_project_header_inside(tmp_paths, tmp_path, monkeypatch):
    proj_dir = tmp_paths.repo / "inner"
    proj_dir.mkdir()
    new_project("inner", path=proj_dir, goal="do a thing",
                paths=tmp_paths)
    add_member("inner", "@lead", role="lead", persistent=True,
               paths=tmp_paths)
    # Point scope inside the project.
    from dataclasses import replace
    scoped = replace(tmp_paths, scope=proj_dir)
    block = _render_project(scoped)
    assert "## Project: inner" in block
    assert "Goal: do a thing" in block
    assert "@lead" in block and ("dormant" in block or "alive" in block)
    assert "Recent:" in block


def test_render_project_empty_members(tmp_paths, tmp_path):
    proj_dir = tmp_paths.repo / "bare"
    proj_dir.mkdir()
    new_project("bare", path=proj_dir, paths=tmp_paths)
    from dataclasses import replace
    scoped = replace(tmp_paths, scope=proj_dir)
    block = _render_project(scoped)
    assert "Members: (none)" in block
