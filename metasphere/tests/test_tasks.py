"""Tests for metasphere.tasks."""

from __future__ import annotations

from pathlib import Path

import pytest

from metasphere import tasks as t


# ---------------------------------------------------------------------------
# slug
# ---------------------------------------------------------------------------


def test_slugify_basic():
    assert t.slugify("Fix login bug") == "fix-login-bug"


def test_slugify_strips_punctuation():
    assert t.slugify("Hello, World!!!") == "hello-world"


def test_slugify_replaces_slashes():
    # the bash bug: slashes used to leak into filenames
    assert "/" not in t.slugify("project/agent/task name")
    assert t.slugify("a/b/c") == "a-b-c"


def test_slugify_truncates():
    assert len(t.slugify("x" * 200)) == 60


def test_slugify_empty_falls_back():
    assert t.slugify("!!!!") == "task"


# ---------------------------------------------------------------------------
# create / read roundtrip
# ---------------------------------------------------------------------------


def test_create_and_read(tmp_paths, monkeypatch):
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@creator")
    task = t.create_task("Hello world", "!high", tmp_paths.scope, tmp_paths.repo)
    assert task.title == "Hello world"
    assert task.priority == "!high"
    assert task.status == t.STATUS_PENDING
    assert task.path is not None and task.path.exists()
    assert "/" not in task.path.name.removesuffix(".md")
    assert task.created_by == "@creator"

    raw = task.path.read_text()
    # B2: created_by must be in the on-disk frontmatter; slug must NOT be.
    assert "created_by: @creator" in raw
    assert "\nslug:" not in raw

    reloaded = t.Task.from_text(raw)
    assert reloaded.title == "Hello world"
    assert reloaded.priority == "!high"
    assert reloaded.id == task.id
    assert reloaded.created_by == "@creator"
    # slug remains accessible at runtime as a property mirroring id
    assert reloaded.slug == reloaded.id


def test_create_invalid_priority(tmp_paths):
    with pytest.raises(ValueError):
        t.create_task("x", "!whatever", tmp_paths.scope, tmp_paths.repo)


def test_create_unique_slug_collision(tmp_paths):
    a = t.create_task("Same title", "!normal", tmp_paths.scope, tmp_paths.repo)
    b = t.create_task("Same title", "!normal", tmp_paths.scope, tmp_paths.repo)
    assert a.id != b.id


# ---------------------------------------------------------------------------
# update preserves frontmatter
# ---------------------------------------------------------------------------


def test_update_preserves_frontmatter(tmp_paths):
    task = t.create_task("update me", "!normal", tmp_paths.scope, tmp_paths.repo)
    t.update_task(task.id, tmp_paths.repo, status="blocked", note="hit a wall")
    reloaded = t.Task.from_text(task.path.read_text())
    assert reloaded.status == "blocked"
    assert reloaded.title == "update me"
    assert reloaded.priority == "!normal"
    assert reloaded.id == task.id
    assert "hit a wall" in reloaded.body


def test_start_task(tmp_paths):
    task = t.create_task("startable", "!normal", tmp_paths.scope, tmp_paths.repo)
    started = t.start_task(task.id, "@worker", tmp_paths.repo)
    assert started.status == t.STATUS_IN_PROGRESS
    assert started.assignee == "@worker"
    assert started.started


# ---------------------------------------------------------------------------
# complete moves file
# ---------------------------------------------------------------------------


def test_complete_task_moves_file(tmp_paths):
    task = t.create_task("ship it", "!normal", tmp_paths.scope, tmp_paths.repo)
    active_path = task.path
    done = t.complete_task(task.id, "shipped", tmp_paths.repo)
    assert done.status == t.STATUS_COMPLETED
    assert done.completed
    assert not active_path.exists()
    assert done.path.exists()
    assert done.path.parent.name == "completed"
    assert "shipped" in done.path.read_text()


# ---------------------------------------------------------------------------
# list across nested scopes (upward visibility)
# ---------------------------------------------------------------------------


def test_list_tasks_nested_scopes(tmp_paths):
    root_scope = tmp_paths.repo
    child_scope = tmp_paths.repo / "subsystem"
    child_scope.mkdir(parents=True)

    root_task = t.create_task("root task", "!normal", root_scope, tmp_paths.repo)
    child_task = t.create_task("child task", "!high", child_scope, tmp_paths.repo)

    # From child scope: see both (upward visibility)
    visible = t.list_tasks(child_scope, tmp_paths.repo)
    titles = {x.title for x in visible}
    assert {"root task", "child task"} <= titles

    # From root scope: only root task
    visible_root = t.list_tasks(root_scope, tmp_paths.repo)
    titles_root = {x.title for x in visible_root}
    assert "root task" in titles_root
    assert "child task" not in titles_root


def test_list_tasks_excludes_completed_by_default(tmp_paths):
    a = t.create_task("a", "!normal", tmp_paths.scope, tmp_paths.repo)
    b = t.create_task("b", "!normal", tmp_paths.scope, tmp_paths.repo)
    t.complete_task(a.id, "done", tmp_paths.repo)

    active = t.list_tasks(tmp_paths.scope, tmp_paths.repo)
    assert {x.title for x in active} == {"b"}

    everything = t.list_tasks(tmp_paths.scope, tmp_paths.repo, include_completed=True)
    assert {x.title for x in everything} == {"a", "b"}
