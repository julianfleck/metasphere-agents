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
    # slashes must not leak into filenames
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
    task = t.create_task("Hello world", "!high", tmp_paths.scope, tmp_paths.project_root)
    assert task.title == "Hello world"
    assert task.priority == "!high"
    assert task.status == t.STATUS_PENDING
    assert task.path is not None and task.path.exists()
    assert "/" not in task.path.name.removesuffix(".md")
    assert task.created_by == "@creator"

    raw = task.path.read_text()
    # B2: created_by must be in the on-disk frontmatter; slug must NOT be.
    # created_by must be in the frontmatter; as of the YAML-safety fix,
    # ``@agent`` values are written quoted.
    assert 'created_by: "@creator"' in raw
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
        t.create_task("x", "!whatever", tmp_paths.scope, tmp_paths.project_root)


def test_create_unique_slug_collision(tmp_paths):
    a = t.create_task("Same title", "!normal", tmp_paths.scope, tmp_paths.project_root)
    b = t.create_task("Same title", "!normal", tmp_paths.scope, tmp_paths.project_root)
    assert a.id != b.id


# ---------------------------------------------------------------------------
# update preserves frontmatter
# ---------------------------------------------------------------------------


def test_update_preserves_frontmatter(tmp_paths):
    task = t.create_task("update me", "!normal", tmp_paths.scope, tmp_paths.project_root)
    t.update_task(task.id, tmp_paths.project_root, status="blocked", note="hit a wall")
    reloaded = t.Task.from_text(task.path.read_text())
    assert reloaded.status == "blocked"
    assert reloaded.title == "update me"
    assert reloaded.priority == "!normal"
    assert reloaded.id == task.id
    assert "hit a wall" in reloaded.body


def test_start_task(tmp_paths):
    task = t.create_task("startable", "!normal", tmp_paths.scope, tmp_paths.project_root)
    started = t.start_task(task.id, "@worker", tmp_paths.project_root)
    assert started.status == t.STATUS_IN_PROGRESS
    assert started.assignee == "@worker"
    assert started.started


# ---------------------------------------------------------------------------
# complete moves file
# ---------------------------------------------------------------------------


def test_complete_task_archives_to_dated_dir(tmp_paths):
    task = t.create_task("ship it", "!normal", tmp_paths.scope, tmp_paths.project_root)
    active_path = task.path
    done = t.complete_task(task.id, "shipped", tmp_paths.project_root)
    assert done.status == t.STATUS_COMPLETED
    assert done.completed
    assert done.updated == done.completed
    assert not active_path.exists()
    assert done.path.exists()
    # archive/YYYY-MM-DD/<slug>.md
    assert done.path.parent.parent.name == "archive"
    day = done.path.parent.name
    assert len(day) == 10 and day[4] == "-" and day[7] == "-"
    assert "shipped" in done.path.read_text()


def test_find_task_includes_archive_and_legacy_completed(tmp_paths):
    # fresh completion goes to archive/ and is findable
    a = t.create_task("archived one", "!normal", tmp_paths.scope, tmp_paths.project_root)
    t.complete_task(a.id, "done", tmp_paths.project_root)
    assert t._find_task_file(a.id) is not None

    # legacy completed/ is still findable
    b = t.create_task("legacy one", "!normal", tmp_paths.scope, tmp_paths.project_root)
    legacy_dir = b.path.parent.parent / "completed"
    legacy_dir.mkdir(parents=True, exist_ok=True)
    import shutil as _sh
    dest = legacy_dir / b.path.name
    _sh.move(str(b.path), str(dest))
    assert t._find_task_file(b.id) == dest


def test_list_includes_archive_when_completed_requested(tmp_paths):
    a = t.create_task("a", "!normal", tmp_paths.scope, tmp_paths.project_root)
    t.create_task("b", "!normal", tmp_paths.scope, tmp_paths.project_root)
    t.complete_task(a.id, "done", tmp_paths.project_root)
    active = t.list_tasks(tmp_paths.scope, tmp_paths.project_root)
    assert {x.title for x in active} == {"b"}
    everything = t.list_tasks(tmp_paths.scope, tmp_paths.project_root, include_completed=True)
    assert {x.title for x in everything} == {"a", "b"}


def test_create_sets_updated_at(tmp_paths):
    task = t.create_task("u", "!normal", tmp_paths.scope, tmp_paths.project_root)
    raw = task.path.read_text()
    assert "updated_at:" in raw
    assert task.updated == task.created


def test_start_bumps_updated(tmp_paths):
    task = t.create_task("u2", "!normal", tmp_paths.scope, tmp_paths.project_root)
    started = t.start_task(task.id, "@w", tmp_paths.project_root)
    assert started.updated == started.started


# ---------------------------------------------------------------------------
# list across nested scopes (upward visibility)
# ---------------------------------------------------------------------------


def test_list_tasks_project_scoped_sees_all(tmp_paths):
    """Under the canonical layout every project owns exactly one
    ``.tasks/`` dir, regardless of which subdirectory of the repo the
    task was created from. So both a task made at the repo root and a
    task made from a subsystem/ child dir land in the same project
    ``.tasks/`` and show up in ``list_tasks`` from either scope.

    This replaces the old ``nested_scopes`` visibility test, whose
    premise (per-subdir ``.tasks/`` trees) no longer applies.
    """
    root_scope = tmp_paths.project_root
    child_scope = tmp_paths.project_root / "subsystem"
    child_scope.mkdir(parents=True)

    t.create_task("root task", "!normal", root_scope, tmp_paths.project_root)
    t.create_task("child task", "!high", child_scope, tmp_paths.project_root)

    for view_from in (root_scope, child_scope):
        visible = t.list_tasks(view_from, tmp_paths.project_root)
        titles = {x.title for x in visible}
        assert {"root task", "child task"} <= titles, (
            f"expected both tasks visible from {view_from}, got {titles}"
        )


def test_list_tasks_excludes_completed_by_default(tmp_paths):
    a = t.create_task("a", "!normal", tmp_paths.scope, tmp_paths.project_root)
    b = t.create_task("b", "!normal", tmp_paths.scope, tmp_paths.project_root)
    t.complete_task(a.id, "done", tmp_paths.project_root)

    active = t.list_tasks(tmp_paths.scope, tmp_paths.project_root)
    assert {x.title for x in active} == {"b"}

    everything = t.list_tasks(tmp_paths.scope, tmp_paths.project_root, include_completed=True)
    assert {x.title for x in everything} == {"a", "b"}


# ---------------------------------------------------------------------------
# Assignment + project hardening
# ---------------------------------------------------------------------------


def test_create_autofills_assignee_from_env(tmp_paths, monkeypatch):
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@alice")
    task = t.create_task("owned", "!normal", tmp_paths.scope, tmp_paths.project_root)
    assert task.assignee == "@alice"
    # The tmp_paths fixture registers the scope as project "testproj",
    # so auto-resolution now fills ``project`` from the registry.
    assert task.project == "testproj"
    raw = task.path.read_text()
    assert 'assigned_to: "@alice"' in raw
    assert "project: testproj" in raw


def test_create_explicit_project_and_assignee(tmp_paths, monkeypatch):
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@alice")
    task = t.create_task(
        "explicit", "!normal", tmp_paths.scope, tmp_paths.project_root,
        project="recurse", assigned_to="@bob",
    )
    assert task.project == "recurse"
    assert task.assignee == "@bob"


def test_create_autofills_project_from_scope(tmp_paths, monkeypatch):
    # Create a fake project at scope
    from metasphere import project as _project
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@carol")
    # Canonical layout (PR #10): project.json lives at
    # ~/.metasphere/projects/<name>/project.json, and the registry
    # maps the repo path to the name. Write both.
    import json as _json
    pdir = tmp_paths.projects / "demoproj"
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "project.json").write_text(_json.dumps({
        "schema": 2, "name": "demoproj", "path": str(tmp_paths.scope),
        "created": "", "status": "active",
    }))
    # Append demoproj to the registry (conftest already created it with
    # testproj; for this test we want demoproj to be the project the
    # scope resolves to, so use a deeper path to make longest-match win).
    reg_file = tmp_paths.root / "projects.json"
    reg = _json.loads(reg_file.read_text())
    # Use a subdirectory so Project.for_cwd picks demoproj over testproj
    # via longest-path match.
    demo_scope = tmp_paths.scope / "demoproj-sub"
    demo_scope.mkdir(parents=True, exist_ok=True)
    reg.append({
        "name": "demoproj", "path": str(demo_scope),
        "registered": "1970-01-01T00:00:00Z",
    })
    reg_file.write_text(_json.dumps(reg))
    # Canonical project.json path for demoproj references demo_scope.
    (pdir / "project.json").write_text(_json.dumps({
        "schema": 2, "name": "demoproj", "path": str(demo_scope),
        "created": "", "status": "active",
    }))
    task = t.create_task("inproj", "!normal", demo_scope, tmp_paths.project_root)
    assert task.project == "demoproj"


def test_assign_task_updates_assignee(tmp_paths, monkeypatch):
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@owner")
    task = t.create_task("a", "!normal", tmp_paths.scope, tmp_paths.project_root)
    updated = t.assign_task(task.id, "@dave", tmp_paths.project_root)
    assert updated.assignee == "@dave"
    # idempotent-ish: normalizes missing @
    updated2 = t.assign_task(task.id, "eve", tmp_paths.project_root)
    assert updated2.assignee == "@eve"


def test_move_task_project(tmp_paths, monkeypatch):
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@owner")
    task = t.create_task("a", "!normal", tmp_paths.scope, tmp_paths.project_root)
    # tmp_paths fixture auto-registers scope as "testproj".
    assert task.project == "testproj"
    moved = t.move_task_project(task.id, "recurse", tmp_paths.project_root)
    assert moved.project == "recurse"


def test_legacy_task_without_project_field_loads_as_default(tmp_paths):
    # Simulate a pre-migration task file lacking 'project:' frontmatter.
    active = tmp_paths.scope / ".tasks" / "active"
    active.mkdir(parents=True, exist_ok=True)
    legacy = active / "legacy.md"
    legacy.write_text(
        "---\nid: legacy\ntitle: legacy one\npriority: !normal\nstatus: pending\n"
        "scope: /\ncreated: 2025-01-01T00:00:00Z\nupdated_at: 2025-01-01T00:00:00Z\n"
        "---\n\nbody\n"
    )
    loaded = t.Task.from_text(legacy.read_text())
    assert loaded.project == "default"


def test_cli_new_warns_and_defaults(tmp_paths, monkeypatch, capsys):
    from metasphere.cli import tasks as cli_tasks
    monkeypatch.delenv("METASPHERE_AGENT_ID", raising=False)
    # Clear the tmp_paths-auto-registered ``testproj`` so auto_project
    # really does return "default" — the warning fires only when the
    # scope doesn't match any registered project.
    (tmp_paths.root / "projects.json").write_text("[]")
    rc = cli_tasks._cmd_new(["write docs"])
    assert rc == 0
    err = capsys.readouterr().err
    assert "no --project" in err
    assert "METASPHERE_AGENT_ID" in err


def test_cli_list_filters(tmp_paths, monkeypatch, capsys):
    from metasphere.cli import tasks as cli_tasks
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@alice")
    t.create_task("alpha", "!normal", tmp_paths.scope, tmp_paths.project_root,
                  project="recurse", assigned_to="@alice")
    t.create_task("beta", "!normal", tmp_paths.scope, tmp_paths.project_root,
                  project="default", assigned_to="@bob")
    t.create_task("gamma", "!normal", tmp_paths.scope, tmp_paths.project_root,
                  project="default", assigned_to="@unassigned")
    capsys.readouterr()
    cli_tasks._cmd_list(["--project", "recurse"])
    out = capsys.readouterr().out
    assert "alpha" in out and "beta" not in out and "gamma" not in out
    cli_tasks._cmd_list(["--owner", "@bob"])
    out = capsys.readouterr().out
    assert "beta" in out and "alpha" not in out
    cli_tasks._cmd_list(["--unassigned"])
    out = capsys.readouterr().out
    assert "gamma" in out and "alpha" not in out


def test_cli_list_project_redirect_from_outside_scope(tmp_path, monkeypatch, capsys):
    """--project <name> must resolve to the registered project's path even
    when the CWD/scope lives outside that project (the Telegram-gateway
    case where the gateway's CWD has no ``.tasks/``)."""
    from metasphere.cli import tasks as cli_tasks
    from metasphere import tasks as _t

    # Simulate ~/.metasphere (no project here — "gateway CWD"-like).
    home = tmp_path / "metasphere"
    home.mkdir()
    outside_scope = tmp_path / "nowhere"
    outside_scope.mkdir()

    # Real project lives at a separate path.
    project_path = tmp_path / "repos" / "worldwire"
    project_path.mkdir(parents=True)

    # Register it in projects.json.
    (home / "projects.json").write_text(
        '[{"name": "worldwire", "path": "' + str(project_path)
        + '", "registered": "2026-04-14T00:00:00Z"}]'
    )

    # Point env at the "outside" scope (no .tasks/ here). Must precede
    # create_task so the canonical-layout lookup resolves against the
    # right METASPHERE_DIR / registry.
    monkeypatch.setenv("METASPHERE_DIR", str(home))
    monkeypatch.setenv("METASPHERE_PROJECT_ROOT", str(outside_scope))
    monkeypatch.setenv("METASPHERE_SCOPE", str(outside_scope))
    monkeypatch.chdir(outside_scope)
    from metasphere import paths as _paths
    _paths._project_root_cache.clear()

    # Seed a task in the project; routes to home/projects/worldwire/.tasks/.
    _t.create_task(
        "alpha-outside", "!normal", project_path, project_path,
        project="worldwire", assigned_to="@someone",
    )

    capsys.readouterr()
    rc = cli_tasks._cmd_list(["--project", "worldwire"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "alpha-outside" in out, out


def test_cli_list_project_unknown_is_noop(tmp_paths, monkeypatch, capsys):
    """Unknown --project name falls through to the existing filter branch
    (tasks from current scope, then filtered by name) — safety net."""
    from metasphere.cli import tasks as cli_tasks
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@alice")
    t.create_task("one", "!normal", tmp_paths.scope, tmp_paths.project_root,
                  project="default", assigned_to="@alice")
    capsys.readouterr()
    rc = cli_tasks._cmd_list(["--project", "does-not-exist"])
    # Prints "no active tasks in scope" rather than crashing.
    assert rc == 0
    out = capsys.readouterr().out
    assert "no active tasks" in out


def _make_two_project_registry(tmp_path, monkeypatch):
    """Build a two-project registry layout and return
    (home, outside_scope, proj_a_path, proj_b_path)."""
    from metasphere import tasks as _t

    home = tmp_path / "metasphere"
    home.mkdir()
    outside_scope = tmp_path / "gateway-cwd"
    outside_scope.mkdir()

    proj_a = tmp_path / "repos" / "worldwire"
    proj_b = tmp_path / "repos" / "metasphere-agents"
    proj_a.mkdir(parents=True)
    proj_b.mkdir(parents=True)

    (home / "projects.json").write_text(
        '[{"name": "worldwire", "path": "' + str(proj_a)
        + '", "registered": "2026-04-14T00:00:00Z"},'
        '{"name": "metasphere-agents", "path": "' + str(proj_b)
        + '", "registered": "2026-04-14T00:00:00Z"}]'
    )

    # Env must be set BEFORE create_task so ``_project_tasks_dir`` sees
    # the right METASPHERE_DIR when looking up registry entries. Prior
    # to the canonical-layout refactor, tasks wrote to ``<scope>/.tasks/``
    # regardless of env, so ordering didn't matter.
    monkeypatch.setenv("METASPHERE_DIR", str(home))
    monkeypatch.setenv("METASPHERE_PROJECT_ROOT", str(outside_scope))
    monkeypatch.setenv("METASPHERE_SCOPE", str(outside_scope))
    monkeypatch.chdir(outside_scope)
    from metasphere import paths as _paths
    _paths._project_root_cache.clear()

    # Seed tasks in each project; routes to ``home/projects/<name>/.tasks/``.
    _t.create_task("alpha-ww", "!high", proj_a, proj_a,
                   project="worldwire", assigned_to="@alice")
    _t.create_task("beta-ww", "!normal", proj_a, proj_a,
                   project="worldwire", assigned_to="@alice")
    _t.create_task("one-ma", "!normal", proj_b, proj_b,
                   project="metasphere-agents", assigned_to="@bob")

    return home, outside_scope, proj_a, proj_b


def test_cli_list_all_projects_fallback(tmp_path, monkeypatch, capsys):
    """Bare `task list` outside any project walks the registry and renders
    condensed output grouped by project."""
    from metasphere.cli import tasks as cli_tasks

    _make_two_project_registry(tmp_path, monkeypatch)

    capsys.readouterr()
    rc = cli_tasks._cmd_list([])
    assert rc == 0
    out = capsys.readouterr().out
    # All three tasks show up
    assert "alpha-ww" in out
    assert "beta-ww" in out
    assert "one-ma" in out
    # Grouped under per-project headers with counts
    assert "worldwire (2)" in out
    assert "metasphere-agents (1)" in out
    # Condensed formatting, not card formatting
    assert "Created:" not in out
    assert "Owner:" not in out


def test_cli_list_condensed_flag_with_project_filter(tmp_path, monkeypatch, capsys):
    """`--condensed` forces one-line view even with a --project filter."""
    from metasphere.cli import tasks as cli_tasks

    _make_two_project_registry(tmp_path, monkeypatch)

    capsys.readouterr()
    rc = cli_tasks._cmd_list(["--project", "worldwire", "--condensed"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "alpha-ww" in out and "beta-ww" in out
    assert "one-ma" not in out
    # Condensed, no card metadata lines
    assert "Created:" not in out
    assert "Owner:" not in out


def test_cli_list_c_shortform_flag(tmp_path, monkeypatch, capsys):
    """`-c` is the short form of --condensed."""
    from metasphere.cli import tasks as cli_tasks

    _make_two_project_registry(tmp_path, monkeypatch)

    capsys.readouterr()
    rc = cli_tasks._cmd_list(["--project", "worldwire", "-c"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "alpha-ww" in out
    assert "Created:" not in out


def test_cli_list_project_scoped_still_expanded(tmp_path, monkeypatch, capsys):
    """Sanity check: `--project` without `--condensed` still yields the
    expanded card view. Guards against the fallback accidentally capturing
    the filtered path."""
    from metasphere.cli import tasks as cli_tasks

    _make_two_project_registry(tmp_path, monkeypatch)

    capsys.readouterr()
    rc = cli_tasks._cmd_list(["--project", "worldwire"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "alpha-ww" in out
    # Expanded card metadata must be present
    assert "Created:" in out
    assert "Owner:" in out
