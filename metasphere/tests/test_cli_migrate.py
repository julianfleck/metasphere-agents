"""Tests for ``metasphere migrate-project-dirs``.

Covers the conflict-detecting one-shot migrator that moves
per-project dirs (``.tasks/`` / ``.messages/`` / ``.changelog/`` /
``.learnings/``) from the registered repo into
``~/.metasphere/projects/<name>/``.

Load-bearing primitives:
  * ``_plan_move`` — decides skip vs. move vs. conflict from src/dst state
  * ``_iter_targets`` — fans ``--what all`` out to every dirname
  * ``_merge_tree`` — performs the actual move under the planner's safety
  * ``_run_migration`` — orchestration: registry filter, dry-run vs apply,
    rc=2 on any unresolvable conflict
  * ``main`` — argparse surface
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from metasphere.cli import migrate as migrate_mod
from metasphere.paths import Paths


# --- _plan_move --------------------------------------------------------

def test_plan_move_src_missing(tmp_path):
    src = tmp_path / "missing"
    dst = tmp_path / "dst"
    action, reason = migrate_mod._plan_move(src, dst)
    assert action == "skip"
    assert "missing" in reason


def test_plan_move_src_empty(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    dst = tmp_path / "dst"
    action, reason = migrate_mod._plan_move(src, dst)
    assert action == "skip"
    assert "empty" in reason


def test_plan_move_dst_missing_returns_move(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.md").write_text("x")
    dst = tmp_path / "dst"
    action, reason = migrate_mod._plan_move(src, dst)
    assert action == "move"
    assert "missing" in reason


def test_plan_move_dst_empty_returns_move(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.md").write_text("x")
    dst = tmp_path / "dst"
    dst.mkdir()
    action, reason = migrate_mod._plan_move(src, dst)
    assert action == "move"
    assert "empty" in reason


def test_plan_move_both_populated_returns_conflict(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.md").write_text("x")
    dst = tmp_path / "dst"
    dst.mkdir()
    (dst / "b.md").write_text("y")
    action, reason = migrate_mod._plan_move(src, dst)
    assert action == "conflict"
    assert "both" in reason


def test_plan_move_src_is_file_not_dir_returns_skip(tmp_path):
    src = tmp_path / "src"
    src.write_text("not a dir")
    dst = tmp_path / "dst"
    action, _ = migrate_mod._plan_move(src, dst)
    assert action == "skip"


# --- _iter_targets -----------------------------------------------------

def test_iter_targets_single():
    assert tuple(migrate_mod._iter_targets("tasks")) == ("tasks",)


def test_iter_targets_all_fans_out():
    assert tuple(migrate_mod._iter_targets("all")) == (
        "tasks", "messages", "changelog", "learnings",
    )


# --- _merge_tree -------------------------------------------------------

def test_merge_tree_dst_missing_moves_whole_tree(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.md").write_text("alpha")
    nested = src / "sub"
    nested.mkdir()
    (nested / "b.md").write_text("beta")

    dst = tmp_path / "deep" / "dst"
    migrate_mod._merge_tree(src, dst)

    assert not src.exists()
    assert (dst / "a.md").read_text() == "alpha"
    assert (dst / "sub" / "b.md").read_text() == "beta"


def test_merge_tree_dst_empty_replaces(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.md").write_text("alpha")

    dst = tmp_path / "dst"
    dst.mkdir()  # empty
    migrate_mod._merge_tree(src, dst)

    assert not src.exists()
    assert (dst / "a.md").read_text() == "alpha"


# --- _run_migration ----------------------------------------------------

def _make_paths(tmp_path: Path, registry: list[dict]) -> Paths:
    """Build a Paths rooted at ``tmp_path`` with a populated
    ``projects.json`` registry. Mirrors the on-disk shape that
    ``project._load_registry`` reads.
    """
    root = tmp_path / "ms"
    root.mkdir(parents=True, exist_ok=True)
    (root / "projects.json").write_text(json.dumps(registry))
    (root / "projects").mkdir(exist_ok=True)
    return Paths(root=root, project_root=tmp_path, scope=tmp_path)


def test_run_migration_dry_run_does_not_touch_disk(tmp_path, capsys):
    repo = tmp_path / "repo"
    (repo / ".tasks").mkdir(parents=True)
    (repo / ".tasks" / "active.md").write_text("task body")

    paths = _make_paths(tmp_path, [
        {"name": "alpha", "path": str(repo)},
    ])

    rc = migrate_mod._run_migration(
        paths, only_project=None, what="tasks", apply=False,
    )
    out, _ = capsys.readouterr()

    assert rc == 0
    # Source untouched.
    assert (repo / ".tasks" / "active.md").read_text() == "task body"
    # Dest not created.
    assert not (paths.projects / "alpha" / ".tasks").exists()
    assert "DRY-RUN tasks: move" in out
    assert "(dry-run — pass --apply to commit)" in out


def test_run_migration_apply_moves(tmp_path, capsys):
    repo = tmp_path / "repo"
    (repo / ".tasks").mkdir(parents=True)
    (repo / ".tasks" / "active.md").write_text("task body")

    paths = _make_paths(tmp_path, [
        {"name": "alpha", "path": str(repo)},
    ])

    rc = migrate_mod._run_migration(
        paths, only_project=None, what="tasks", apply=True,
    )
    out, _ = capsys.readouterr()

    assert rc == 0
    assert not (repo / ".tasks").exists()
    assert (paths.projects / "alpha" / ".tasks" / "active.md").read_text() \
        == "task body"
    assert "APPLY tasks: move" in out
    assert "migration complete." in out


def test_run_migration_unknown_project_returns_2(tmp_path, capsys):
    paths = _make_paths(tmp_path, [
        {"name": "alpha", "path": str(tmp_path / "repo")},
    ])
    rc = migrate_mod._run_migration(
        paths, only_project="nope", what="tasks", apply=True,
    )
    _, err = capsys.readouterr()
    assert rc == 2
    assert "no registered project named 'nope'" in err


def test_run_migration_only_project_filters(tmp_path, capsys):
    repo_a = tmp_path / "repo_a"
    repo_b = tmp_path / "repo_b"
    (repo_a / ".tasks").mkdir(parents=True)
    (repo_a / ".tasks" / "a.md").write_text("a")
    (repo_b / ".tasks").mkdir(parents=True)
    (repo_b / ".tasks" / "b.md").write_text("b")

    paths = _make_paths(tmp_path, [
        {"name": "alpha", "path": str(repo_a)},
        {"name": "beta", "path": str(repo_b)},
    ])

    rc = migrate_mod._run_migration(
        paths, only_project="alpha", what="tasks", apply=True,
    )
    out, _ = capsys.readouterr()

    assert rc == 0
    # alpha migrated, beta untouched.
    assert not (repo_a / ".tasks").exists()
    assert (paths.projects / "alpha" / ".tasks" / "a.md").read_text() == "a"
    assert (repo_b / ".tasks" / "b.md").read_text() == "b"
    assert not (paths.projects / "beta" / ".tasks").exists()
    # Output names alpha but not beta's section header.
    assert "[alpha]" in out
    assert "[beta]" not in out


def test_run_migration_conflict_returns_2_and_does_not_move(tmp_path, capsys):
    repo = tmp_path / "repo"
    (repo / ".tasks").mkdir(parents=True)
    (repo / ".tasks" / "src.md").write_text("from src")

    paths = _make_paths(tmp_path, [
        {"name": "alpha", "path": str(repo)},
    ])
    dst_tasks = paths.projects / "alpha" / ".tasks"
    dst_tasks.mkdir(parents=True)
    (dst_tasks / "dst.md").write_text("from dst")

    rc = migrate_mod._run_migration(
        paths, only_project=None, what="tasks", apply=True,
    )
    out, err = capsys.readouterr()

    assert rc == 2
    # Both sides preserved — operator must resolve.
    assert (repo / ".tasks" / "src.md").read_text() == "from src"
    assert (dst_tasks / "dst.md").read_text() == "from dst"
    assert "conflict" in out
    assert "CONFLICTS" in err


def test_run_migration_all_iterates_every_dirname(tmp_path, capsys):
    repo = tmp_path / "repo"
    for d in (".tasks", ".messages", ".changelog", ".learnings"):
        (repo / d).mkdir(parents=True)
        (repo / d / "x.md").write_text(d)

    paths = _make_paths(tmp_path, [
        {"name": "alpha", "path": str(repo)},
    ])

    rc = migrate_mod._run_migration(
        paths, only_project=None, what="all", apply=True,
    )
    out, _ = capsys.readouterr()

    assert rc == 0
    for tgt, dirname in (
        ("tasks", ".tasks"),
        ("messages", ".messages"),
        ("changelog", ".changelog"),
        ("learnings", ".learnings"),
    ):
        assert f"APPLY {tgt}: move" in out
        assert (paths.projects / "alpha" / dirname / "x.md").read_text() \
            == dirname


def test_run_migration_skips_entries_without_name(tmp_path, capsys):
    """Registry entries with empty ``name`` are silently skipped, not
    crashed on.
    """
    repo = tmp_path / "repo"
    (repo / ".tasks").mkdir(parents=True)
    (repo / ".tasks" / "x.md").write_text("x")

    paths = _make_paths(tmp_path, [
        {"name": "", "path": str(repo)},
        {"name": "alpha", "path": str(repo)},
    ])
    rc = migrate_mod._run_migration(
        paths, only_project=None, what="tasks", apply=False,
    )
    out, _ = capsys.readouterr()
    assert rc == 0
    assert "[alpha]" in out


# --- main / argparse ---------------------------------------------------

def test_main_dry_run_default(monkeypatch, tmp_path):
    captured: dict = {}

    def fake_run(paths, *, only_project, what, apply):
        captured.update(
            paths=paths, only_project=only_project, what=what, apply=apply,
        )
        return 0

    fake_paths = _make_paths(tmp_path, [])
    monkeypatch.setattr(migrate_mod, "resolve", lambda: fake_paths)
    monkeypatch.setattr(migrate_mod, "_run_migration", fake_run)

    rc = migrate_mod.main([])
    assert rc == 0
    assert captured["only_project"] is None
    assert captured["what"] == "tasks"
    assert captured["apply"] is False


def test_main_passes_flags_through(monkeypatch, tmp_path):
    captured: dict = {}

    def fake_run(paths, *, only_project, what, apply):
        captured.update(
            only_project=only_project, what=what, apply=apply,
        )
        return 0

    monkeypatch.setattr(
        migrate_mod, "resolve", lambda: _make_paths(tmp_path, []),
    )
    monkeypatch.setattr(migrate_mod, "_run_migration", fake_run)

    rc = migrate_mod.main(
        ["--project", "alpha", "--what", "all", "--apply"]
    )
    assert rc == 0
    assert captured == {
        "only_project": "alpha", "what": "all", "apply": True,
    }


def test_main_invalid_what_choice_exits_2(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        migrate_mod, "resolve", lambda: _make_paths(tmp_path, []),
    )
    with pytest.raises(SystemExit) as excinfo:
        migrate_mod.main(["--what", "nonsense"])
    assert excinfo.value.code == 2


def test_main_help_exits_0(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(
        migrate_mod, "resolve", lambda: _make_paths(tmp_path, []),
    )
    rc = migrate_mod.main(["--help"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "metasphere migrate-project-dirs" in out
