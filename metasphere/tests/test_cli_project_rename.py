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


def test_cli_init_help_flag_does_not_register(tmp_paths: Paths):
    """``metasphere project init --help`` previously slipped through
    as ``init_project(path=Path('--help'))`` and polluted projects.json
    with a ghost ``--help`` entry. Argparse intercepts ``--help`` now
    so the CLI raises SystemExit(0) and writes nothing.
    """
    registry = tmp_paths.root / "projects.json"
    before = registry.read_text() if registry.is_file() else ""
    with pytest.raises(SystemExit) as exc:
        _cli_proj._cmd_init(["--help"], tmp_paths)
    assert exc.value.code == 0
    after = registry.read_text() if registry.is_file() else ""
    assert before == after


# ----- flag-shape rejection on lookup-by-name subcommands -----
# Same class of argv-leak as df6812e (init), 478be54 (hooks-git), and
# 8d7d794 (restart): ``project <sub> --foo`` falls through to a name
# lookup that either silently returns "not found" or, in wake's case,
# raises an uncaught FileNotFoundError.


def test_cli_wake_flag_shape_does_not_raise(tmp_paths: Paths, capsys):
    """``project wake --foo`` previously raised an uncaught
    FileNotFoundError from ``wake_members``. Guard catches it before
    the project lookup.
    """
    rc = _cli_proj._cmd_wake(["--foo"], tmp_paths)
    _, err = capsys.readouterr()
    assert rc == 2
    assert "looks like a CLI flag" in err


def test_cli_show_flag_shape_rejected(tmp_paths: Paths, capsys):
    rc = _cli_proj._cmd_show(["--bar"], tmp_paths)
    _, err = capsys.readouterr()
    assert rc == 2
    assert "project show" in err
    assert "'--bar'" in err


def test_cli_rename_flag_shape_rejected_on_either_arg(
    tmp_paths: Paths, capsys,
):
    _setup_project(tmp_paths, "src-proj")
    # leading flag
    rc = _cli_proj._cmd_rename(["--foo", "ok"], tmp_paths)
    _, err = capsys.readouterr()
    assert rc == 2
    assert "looks like a CLI flag" in err
    # trailing flag (e.g., typo in the new name)
    rc = _cli_proj._cmd_rename(["src-proj", "--new"], tmp_paths)
    _, err = capsys.readouterr()
    assert rc == 2
    assert "looks like a CLI flag" in err


def test_cli_chat_flag_shape_rejected(tmp_paths: Paths, capsys):
    rc = _cli_proj._cmd_chat(["--foo", "hi"], tmp_paths)
    _, err = capsys.readouterr()
    assert rc == 2
    assert "looks like a CLI flag" in err


def test_cli_wake_help_prints_usage(tmp_paths: Paths, capsys):
    rc = _cli_proj._cmd_wake(["--help"], tmp_paths)
    out, _ = capsys.readouterr()
    assert rc == 0
    assert "project" in out and "wake" in out


def test_cli_show_help_prints_usage(tmp_paths: Paths, capsys):
    rc = _cli_proj._cmd_show(["-h"], tmp_paths)
    out, _ = capsys.readouterr()
    assert rc == 0
    assert "Usage:" in out


def test_cli_changelog_flag_shape_rejected(tmp_paths: Paths, capsys):
    rc = _cli_proj._cmd_changelog(["--foo"], tmp_paths)
    _, err = capsys.readouterr()
    assert rc == 2
    assert "looks like a CLI flag" in err


def test_cli_learnings_flag_shape_rejected(tmp_paths: Paths, capsys):
    rc = _cli_proj._cmd_learnings(["--foo"], tmp_paths)
    _, err = capsys.readouterr()
    assert rc == 2
    assert "looks like a CLI flag" in err


def test_cli_for_flag_shape_rejected(tmp_paths: Paths, capsys):
    """``project for --foo`` would silently treat ``--foo`` as a path
    and return 0 with no output. Guard surfaces it as a typo.
    """
    rc = _cli_proj._cmd_for(["--foo"], tmp_paths)
    _, err = capsys.readouterr()
    assert rc == 2
    assert "looks like a CLI flag" in err


def test_cli_member_list_flag_shape_rejected(tmp_paths: Paths, capsys):
    rc = _cli_proj._cmd_member(["list", "--foo"], tmp_paths)
    _, err = capsys.readouterr()
    assert rc == 2
    assert "looks like a CLI flag" in err
