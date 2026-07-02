"""Tests for Stage B mention-capsule injection + the empty-state fix
(metasphere/context.py: _render_mentioned_projects, _auto_memory_dir_for_path,
_memory_index_head, and the _render_memory_fts empty-state replacement)."""

import json

import metasphere.context as ctx
from metasphere.paths import Paths
from metasphere.project import Project
from metasphere.tasks import Task, active_tasks_for_project


def _write_task(paths: Paths, project_name: str, **kw) -> None:
    """Drop a task .md into ``<project>/.tasks/active/`` for capsule tests."""
    proj = Project(name=project_name, path=f"/repos/{project_name}")
    active = proj.tasks_dir(paths) / "active"
    active.mkdir(parents=True, exist_ok=True)
    t = Task(**kw)
    (active / f"{t.id}.md").write_text(t.to_text())


def _register(paths: Paths, *entries):
    rows = []
    for e in entries:
        if isinstance(e, str):
            rows.append({"name": e, "path": f"/repos/{e}",
                         "registered": "1970-01-01T00:00:00Z"})
        else:
            rows.append(e)
    (paths.root / "projects.json").write_text(json.dumps(rows))


# --- _auto_memory_dir_for_path -------------------------------------------


def test_auto_memory_dir_encoding(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    out = ctx._auto_memory_dir_for_path("/home/user/projects/widget")
    assert out == (tmp_path / ".claude" / "projects"
                   / "-home-user-projects-widget" / "memory")


def test_auto_memory_dir_empty_path():
    assert ctx._auto_memory_dir_for_path("") is None


# --- _memory_index_head ---------------------------------------------------


def test_memory_index_head_parses_pointer_lines(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir()
    (mem / "MEMORY.md").write_text(
        "# Index\n\n"
        "- [Alpha](a.md) — first hook\n"
        "- [Beta](b.md) — second hook\n"
        "some prose, not a pointer\n"
        "- [Gamma](c.md) — third hook\n"
    )
    out = ctx._memory_index_head(mem, n=2)
    assert out == ["- [Alpha](a.md) — first hook", "- [Beta](b.md) — second hook"]


def test_memory_index_head_missing_file(tmp_path):
    assert ctx._memory_index_head(tmp_path / "nope") == []


def test_memory_index_head_byte_bounds_long_line(tmp_path):
    mem = tmp_path / "memory"
    mem.mkdir()
    (mem / "MEMORY.md").write_text("- [X](x.md) — " + "y" * 500 + "\n")
    out = ctx._memory_index_head(mem)
    assert len(out) == 1 and out[0].endswith("...") and len(out[0]) == 160


# --- _render_mentioned_projects ------------------------------------------


def test_no_prompt_renders_nothing(tmp_paths):
    _register(tmp_paths, "widget")
    assert ctx._render_mentioned_projects(tmp_paths, "@a", "") == ""
    assert ctx._render_mentioned_projects(tmp_paths, "@a", "   ") == ""


def test_no_mention_renders_nothing(tmp_paths):
    _register(tmp_paths, "widget")
    assert ctx._render_mentioned_projects(tmp_paths, "@a", "hello there") == ""


def test_mention_renders_location_and_recent(tmp_paths, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    _register(tmp_paths, {"name": "widget", "path": "/repos/widget",
                          "registered": "1970-01-01T00:00:00Z"})
    mem_dir = ctx._auto_memory_dir_for_path("/repos/widget")
    mem_dir.mkdir(parents=True)
    (mem_dir / "MEMORY.md").write_text("- [DB host](db.md) — port 2323\n")

    out = ctx._render_mentioned_projects(tmp_paths, "@a", "status of widget?")
    assert "## Mentioned projects" in out
    assert "### widget" in out
    assert f"memory: {mem_dir}" in out
    assert "- [DB host](db.md) — port 2323" in out


def test_mention_no_memory_folder_yet(tmp_paths, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))  # folder won't exist
    _register(tmp_paths, {"name": "widget", "path": "/repos/widget",
                          "registered": "1970-01-01T00:00:00Z"})
    out = ctx._render_mentioned_projects(tmp_paths, "@a", "widget please")
    assert "### widget" in out
    assert "no memories written yet" in out


def test_build_context_includes_mentioned_when_named(tmp_paths, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    _register(tmp_paths, {"name": "widget", "path": "/repos/widget",
                          "registered": "1970-01-01T00:00:00Z"})
    out = ctx.build_context(tmp_paths, prompt="what's up with widget")
    assert "## Mentioned projects" in out


def test_build_context_no_mentioned_section_on_heartbeat(tmp_paths, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    _register(tmp_paths, "widget")
    out = ctx.build_context(tmp_paths)  # empty prompt = heartbeat
    assert "## Mentioned projects" not in out


# --- active_tasks_for_project (single-project, no global fold-in) ----------


def test_active_tasks_for_project_reads_only_active(tmp_paths):
    _write_task(tmp_paths, "widget", id="t-open", title="open one",
                priority="!high", status="pending", assignee="@eng")
    _write_task(tmp_paths, "widget", id="t-done", title="closed one",
                priority="!normal", status="completed")
    proj = Project(name="widget", path="/repos/widget")
    out = active_tasks_for_project(proj, tmp_paths)
    ids = {t.id for t in out}
    assert ids == {"t-open"}  # terminal/completed excluded


def test_active_tasks_for_project_missing_dir(tmp_paths):
    proj = Project(name="never-tasked", path="/repos/never-tasked")
    assert active_tasks_for_project(proj, tmp_paths) == []


# --- tasks-in-capsule (Stage B) -------------------------------------------


def test_capsule_includes_open_tasks(tmp_paths, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    _register(tmp_paths, {"name": "widget", "path": "/repos/widget",
                          "registered": "1970-01-01T00:00:00Z"})
    _write_task(tmp_paths, "widget", id="cut-over", title="finish cutover",
                priority="!high", status="pending", assignee="@lead")
    out = ctx._render_mentioned_projects(tmp_paths, "@a", "status of widget?")
    assert "open tasks:" in out
    assert "!high finish cutover [cut-over] → @lead" in out


def test_capsule_omits_tasks_block_when_none(tmp_paths, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    _register(tmp_paths, {"name": "widget", "path": "/repos/widget",
                          "registered": "1970-01-01T00:00:00Z"})
    out = ctx._render_mentioned_projects(tmp_paths, "@a", "widget please")
    assert "### widget" in out
    assert "open tasks:" not in out


def test_capsule_caps_tasks_at_seven(tmp_paths, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    _register(tmp_paths, {"name": "widget", "path": "/repos/widget",
                          "registered": "1970-01-01T00:00:00Z"})
    for i in range(9):
        _write_task(tmp_paths, "widget", id=f"t{i:02d}", title=f"task {i}",
                    priority="!normal", status="pending")
    out = ctx._render_mentioned_projects(tmp_paths, "@a", "widget status")
    assert out.count("  ○ ") == 7
    assert "… +2 more" in out


# --- empty-state fix in _render_memory_fts --------------------------------


def test_empty_state_points_at_memory_folder(tmp_paths, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    # Force the recall to return nothing.
    monkeypatch.setattr(ctx, "_memory_context_for", lambda *a, **k: "", raising=False)
    monkeypatch.setattr("metasphere.memory.context_for", lambda *a, **k: "")
    out = ctx._render_memory_fts(tmp_paths, "@test", "anything")
    assert "No relevant memory found." not in out
    assert "Your memory folder:" in out
    assert "write new memories" in out


# --- Stage C: FTS-suppress-on-match (no double-injection) -----------------


def test_suppress_empty_drops_no_hits_affordance(tmp_paths, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    # No hits — but a project capsule already rendered this turn, so the
    # no-hits affordance is redundant and must be fully suppressed.
    monkeypatch.setattr(ctx, "_memory_context_for", lambda *a, **k: "", raising=False)
    monkeypatch.setattr("metasphere.memory.context_for", lambda *a, **k: "")
    out = ctx._render_memory_fts(tmp_paths, "@test", "anything", suppress_empty=True)
    assert out == ""


def test_suppress_empty_keeps_real_hits(tmp_paths, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    # Real hits are additive ("you might also recall") and must survive
    # suppression — only the empty dead-end is dropped.
    monkeypatch.setattr("metasphere.memory.context_for",
                        lambda *a, **k: "  a real memory hit  ")
    out = ctx._render_memory_fts(tmp_paths, "@test", "anything", suppress_empty=True)
    assert "## Memory Context (FTS)" in out
    assert "a real memory hit" in out


def test_build_context_no_double_inject_on_mention(tmp_paths, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    _register(tmp_paths, {"name": "widget", "path": "/repos/widget",
                          "registered": "1970-01-01T00:00:00Z"})
    # No FTS hits available.
    monkeypatch.setattr(ctx, "_memory_context_for", lambda *a, **k: "", raising=False)
    monkeypatch.setattr("metasphere.memory.context_for", lambda *a, **k: "")
    out = ctx.build_context(tmp_paths, prompt="status of widget?")
    # Capsule renders; the redundant FTS no-hits block does NOT.
    assert "## Mentioned projects" in out
    assert "## Memory Context (FTS)" not in out
    assert "No memories matched this turn" not in out


def test_build_context_keeps_fts_section_without_mention(tmp_paths, monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    _register(tmp_paths, "widget")
    monkeypatch.setattr(ctx, "_memory_context_for", lambda *a, **k: "", raising=False)
    monkeypatch.setattr("metasphere.memory.context_for", lambda *a, **k: "")
    # No project named → no capsule → FTS section (with affordance) stays.
    out = ctx.build_context(tmp_paths, prompt="just a free-form question")
    assert "## Memory Context (FTS)" in out
