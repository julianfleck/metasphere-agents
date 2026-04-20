"""Tests for ``metasphere project rename <old> <new>``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from metasphere import project as _proj
from metasphere.cli import project as _cli_proj
from metasphere.paths import Paths


def _setup_project(paths: Paths, name: str, *, custom_path: str | None = None) -> _proj.Project:
    """Create a minimal project at the default or custom path + register."""
    proj_dir = paths.projects / name
    proj_dir.mkdir(parents=True, exist_ok=True)
    proj = _proj.Project(
        schema=2,
        name=name,
        path=custom_path or str(proj_dir),
        status="active",
    )
    _proj.save_project(proj, paths=paths)
    _proj._register(paths, proj)
    return proj


def test_rename_happy_path_default_dir(tmp_paths: Paths):
    """Rename moves dir + updates metadata when project is at default path."""
    _setup_project(tmp_paths, "foo")

    proj = _proj.rename_project("foo", "bar", paths=tmp_paths)

    assert proj.name == "bar"
    assert str(tmp_paths.projects / "bar") in proj.path
    assert (tmp_paths.projects / "bar" / "project.json").is_file()
    assert not (tmp_paths.projects / "foo").exists()

    # Registry round-trip: bar present, foo absent
    projects = _proj.list_projects(paths=tmp_paths)
    names = {p.name for p in projects}
    assert "bar" in names
    assert "foo" not in names


def test_rename_custom_path_no_dir_move(tmp_paths: Paths, tmp_path: Path):
    """When project has a custom path outside ~/.metasphere/projects/,
    the directory is NOT moved — only metadata updates."""
    custom = tmp_path / "external" / "myproject"
    custom.mkdir(parents=True)
    proj = _setup_project(tmp_paths, "ext", custom_path=str(custom))

    renamed = _proj.rename_project("ext", "ext-v2", paths=tmp_paths)

    assert renamed.name == "ext-v2"
    # Custom path stays unchanged (dir wasn't under projects/)
    assert renamed.path == str(custom)
    # New canonical project.json exists at ext-v2/
    assert (tmp_paths.projects / "ext-v2" / "project.json").is_file()


def test_rename_collision_raises(tmp_paths: Paths):
    """Renaming to an existing name raises FileExistsError."""
    _setup_project(tmp_paths, "alpha")
    _setup_project(tmp_paths, "beta")

    with pytest.raises(FileExistsError, match="already exists"):
        _proj.rename_project("alpha", "beta", paths=tmp_paths)


def test_rename_missing_source_raises(tmp_paths: Paths):
    """Renaming a non-existent project raises FileNotFoundError."""
    with pytest.raises(FileNotFoundError, match="not found"):
        _proj.rename_project("ghost", "new", paths=tmp_paths)


def test_rename_noop_same_name(tmp_paths: Paths):
    """old == new is a noop, not an error."""
    _setup_project(tmp_paths, "same")
    proj = _proj.rename_project("same", "same", paths=tmp_paths)
    assert proj.name == "same"


def test_rename_invalid_name_raises(tmp_paths: Paths):
    """Names with / or null raise ValueError."""
    _setup_project(tmp_paths, "valid")
    with pytest.raises(ValueError, match="invalid"):
        _proj.rename_project("valid", "bad/name", paths=tmp_paths)
    with pytest.raises(ValueError, match="invalid"):
        _proj.rename_project("valid", "bad\x00name", paths=tmp_paths)


def test_cli_rename_happy_path(tmp_paths: Paths, capsys, monkeypatch):
    """CLI integration: project rename returns 0 and prints the new name."""
    _setup_project(tmp_paths, "cli-old")
    monkeypatch.setattr(_cli_proj, "resolve", lambda: tmp_paths)
    rc = _cli_proj._cmd_rename(["cli-old", "cli-new"], tmp_paths)
    out, _ = capsys.readouterr()
    assert rc == 0
    assert "cli-new" in out


def test_cli_rename_missing_args(tmp_paths: Paths, capsys):
    """CLI returns 2 with usage message when args are missing."""
    rc = _cli_proj._cmd_rename([], tmp_paths)
    _, err = capsys.readouterr()
    assert rc == 2
    assert "Usage" in err
