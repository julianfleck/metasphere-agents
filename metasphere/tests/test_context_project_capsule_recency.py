"""Tests for B6 — recency-sort + always-include-file-path-pointer in
project capsule.

B5 (c21e1a7) shipped relevance-rank entry selection from inbox +
MISSION query signal. The 2026-05-29 design directive replaces that
with recency-sort + always-on file-path pointer footer: ranking by
query was over-engineered, wake banner timing made it unreliable,
and recency is predictable. Agents who need older content can grep
the file via the footer pointer.

This test file replaces ``test_context_project_capsule_relevance.py``
(which exercised the deleted B5 surface). Markdown-entry parsing is
shared with B5 and continues to be covered here.
"""

from __future__ import annotations

import os
from pathlib import Path

from metasphere import context as ctx
from metasphere import messages as _msgs
from metasphere.paths import Paths


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------


def _seed_project_nested_agent_mission(
    tmp_paths: Paths, project: str, agent: str, mission_body: str = "body",
) -> Path:
    d = tmp_paths.project_agent_dir(project, agent)
    d.mkdir(parents=True, exist_ok=True)
    (d / "MISSION.md").write_text(
        f"# Mission\n\n{mission_body}\n", encoding="utf-8",
    )
    return d


def _seed_root_agent_mission(
    tmp_paths: Paths, agent: str, frontmatter: str = "",
) -> Path:
    d = tmp_paths.agent_dir(agent)
    d.mkdir(parents=True, exist_ok=True)
    fm_block = ("---\n" + frontmatter + "---\n\n") if frontmatter else ""
    (d / "MISSION.md").write_text(
        fm_block + "# Mission\n\nbody\n", encoding="utf-8",
    )
    return d


def _seed_project_file(
    tmp_paths: Paths, project: str, filename: str, content: str,
) -> Path:
    pdir = tmp_paths.projects / project
    pdir.mkdir(parents=True, exist_ok=True)
    fpath = pdir / filename
    fpath.write_text(content, encoding="utf-8")
    return fpath


def _seed_inbox_message(
    tmp_paths: Paths, body: str, target: str,
) -> None:
    _msgs.send_message(
        target=target,
        label="!task",
        body=body,
        from_agent="@test-sender",
        paths=tmp_paths,
        wake=False,
    )


# ===========================================================================
# Unit: _parse_markdown_entries (carried over from B5 — parser unchanged)
# ===========================================================================


def test_parse_markdown_entries_basic():
    text = (
        "## First\n\nbody one\n\n"
        "### Sub\n\nbody two\n\n"
        "## Third\n\nbody three\n"
    )
    out = ctx._parse_markdown_entries(text)
    assert [h for h, _b in out] == ["## First", "### Sub", "## Third"]
    assert [b for _h, b in out] == ["body one", "body two", "body three"]


def test_parse_markdown_entries_no_headers():
    out = ctx._parse_markdown_entries("flat text\nlines\n")
    assert out == [("", "flat text\nlines")]


def test_parse_markdown_entries_empty_input():
    assert ctx._parse_markdown_entries("") == []


# ===========================================================================
# Unit: _sort_entries_by_recency
# ===========================================================================


def test_recency_sort_basic():
    entries = [
        ("## 2026-01-15: alpha", "a"),
        ("## 2026-03-10: bravo", "b"),
        ("## 2026-02-22: charlie", "c"),
        ("## 2025-12-01: delta", "d"),
        ("## 2026-04-07: echo", "e"),
    ]
    out = ctx._sort_entries_by_recency(entries)
    headers = [h for h, _ in out]
    assert headers == [
        "## 2026-04-07: echo",
        "## 2026-03-10: bravo",
        "## 2026-02-22: charlie",
        "## 2026-01-15: alpha",
        "## 2025-12-01: delta",
    ]


def test_recency_sort_date_less_entries_after_dated():
    entries = [
        ("## undated alpha", "a"),
        ("## 2026-03-10: bravo", "b"),
        ("## undated charlie", "c"),
        ("## 2026-01-05: delta", "d"),
    ]
    out = ctx._sort_entries_by_recency(entries)
    headers = [h for h, _ in out]
    # Dated entries first (newest→oldest), undated after in original
    # file order.
    assert headers == [
        "## 2026-03-10: bravo",
        "## 2026-01-05: delta",
        "## undated alpha",
        "## undated charlie",
    ]


def test_recency_sort_no_dates_preserves_file_order():
    entries = [
        ("## alpha", "a"),
        ("## bravo", "b"),
        ("## charlie", "c"),
    ]
    out = ctx._sort_entries_by_recency(entries)
    assert [h for h, _ in out] == ["## alpha", "## bravo", "## charlie"]


def test_recency_sort_same_date_stable_tiebreak():
    entries = [
        ("## 2026-05-29: first", "a"),
        ("## 2026-05-29: second", "b"),
        ("## 2026-05-29: third", "c"),
    ]
    out = ctx._sort_entries_by_recency(entries)
    # Same date → original file order preserved.
    assert [h for h, _ in out] == [
        "## 2026-05-29: first",
        "## 2026-05-29: second",
        "## 2026-05-29: third",
    ]


def test_recency_sort_h1_preamble_treated_as_undated():
    # ``("", body)`` from a preamble has no header → no date →
    # buckets at end.
    entries = [
        ("", "# preamble title"),
        ("## 2026-05-29: real", "r"),
    ]
    out = ctx._sort_entries_by_recency(entries)
    assert out[0] == ("## 2026-05-29: real", "r")
    assert out[1] == ("", "# preamble title")


# ===========================================================================
# Unit: _build_footer
# ===========================================================================


def test_footer_includes_real_path_when_truncated(tmp_path: Path):
    path = tmp_path / "LEARNINGS.md"
    path.write_text("placeholder", encoding="utf-8")
    out = ctx._build_footer(omitted=42, file_path=path)
    assert str(path) in out
    assert "42 more" in out
    assert "entries omitted" in out


def test_footer_includes_real_path_when_not_truncated(tmp_path: Path):
    path = tmp_path / "MEMORY.md"
    path.write_text("placeholder", encoding="utf-8")
    out = ctx._build_footer(omitted=0, file_path=path)
    assert str(path) in out
    assert "more" not in out  # no "N more" phrasing in non-truncated case
    assert "Full file" in out


def test_footer_singular_for_one_entry(tmp_path: Path):
    path = tmp_path / "L.md"
    path.write_text("x", encoding="utf-8")
    out = ctx._build_footer(omitted=1, file_path=path)
    assert "1 more entry" in out  # singular
    assert "entries" not in out


# ===========================================================================
# Unit: _render_project_file
# ===========================================================================


def test_render_project_file_recency_orders_newest_first(tmp_path: Path):
    content = (
        "## 2025-12-01: old entry\n\nold body content\n\n"
        "## 2026-05-29: new entry\n\nnew body content\n\n"
        "## 2026-03-10: mid entry\n\nmid body content\n"
    )
    path = tmp_path / "L.md"
    path.write_text(content, encoding="utf-8")
    out = ctx._render_project_file(path, budget=2048)
    # Newest entry must appear before older ones in the rendered text.
    new_idx = out.find("new entry")
    mid_idx = out.find("mid entry")
    old_idx = out.find("old entry")
    assert new_idx >= 0 and mid_idx > new_idx and old_idx > mid_idx


def test_render_project_file_footer_always_present(tmp_path: Path):
    # Few entries, all fit → footer with file path still appears.
    content = (
        "## 2026-05-29: a\n\nbody a\n\n"
        "## 2026-05-28: b\n\nbody b\n"
    )
    path = tmp_path / "LEARNINGS.md"
    path.write_text(content, encoding="utf-8")
    out = ctx._render_project_file(path, budget=2048)
    assert "Full file" in out
    assert str(path) in out


def test_render_project_file_truncated_footer_cites_omitted_count(
    tmp_path: Path,
):
    bulk = "filler word " * 18  # ~216B per body
    content = "\n\n".join(
        f"## 2026-{(i % 12) + 1:02d}-15: entry {i}\n\n{bulk}"
        for i in range(10)
    )
    path = tmp_path / "L.md"
    path.write_text(content, encoding="utf-8")
    out = ctx._render_project_file(path, budget=600)
    assert "more entries omitted" in out
    assert str(path) in out


def test_render_project_file_footer_path_is_real_and_grep_compatible(
    tmp_path: Path,
):
    content = "## 2026-05-29: x\n\nbody\n"
    path = tmp_path / "LEARNINGS.md"
    path.write_text(content, encoding="utf-8")
    out = ctx._render_project_file(path, budget=2048)
    # Extract the path from the footer; it MUST exist on disk.
    # Format: "Full file: <path> —"
    marker = "Full file: "
    start = out.index(marker) + len(marker)
    end = out.index(" —", start)
    pointer = out[start:end]
    assert os.path.exists(pointer)
    assert pointer == str(path)


def test_render_project_file_missing_returns_empty(tmp_path: Path):
    assert ctx._render_project_file(tmp_path / "missing.md", 2048) == ""


def test_render_project_file_unstructured_falls_back_with_footer(
    tmp_path: Path,
):
    content = "flat prose without headers\n"
    path = tmp_path / "L.md"
    path.write_text(content, encoding="utf-8")
    out = ctx._render_project_file(path, budget=2048)
    assert "flat prose" in out
    assert "Full file" in out
    assert str(path) in out


def test_render_project_file_degenerate_budget_footer_wins(tmp_path: Path):
    # Budget too small to fit any entry alongside the footer.
    bulk = "filler " * 100  # ~700B
    content = (
        f"## 2026-05-29: a\n\n{bulk}\n\n"
        f"## 2026-05-28: b\n\n{bulk}\n"
    )
    path = tmp_path / "L.md"
    path.write_text(content, encoding="utf-8")
    out = ctx._render_project_file(path, budget=100)
    # No entries fit — but the footer MUST still appear so the agent
    # knows where the file is.
    assert "Full file" in out or "more entries" in out
    assert str(path) in out


# ===========================================================================
# Integration: _render_project_capsule end-to-end
# ===========================================================================


def test_capsule_inbox_independence(tmp_paths: Paths):
    """B5's relevance machinery read from the inbox; B6 must NOT.
    Populating the inbox should produce IDENTICAL output to leaving
    it empty — proving the relevance-rank code is gone."""
    _seed_project_nested_agent_mission(
        tmp_paths, "widget", "@indep-probe",
    )
    content = "\n\n".join(
        f"## 2026-{(i % 12) + 1:02d}-15: entry {i}\n\nbody {i}"
        for i in range(8)
    )
    _seed_project_file(tmp_paths, "widget", "LEARNINGS.md", content)

    out_empty_inbox = ctx._render_project_capsule(
        tmp_paths, "@indep-probe",
    )

    # Now flood the inbox with content that would have moved B5
    # scoring around.
    for body in (
        "alpha beta gamma delta", "epsilon zeta eta",
        "hetzner tunnel timeout", "consolidate dormancy reap",
    ):
        _seed_inbox_message(tmp_paths, body, "@indep-probe")

    out_full_inbox = ctx._render_project_capsule(
        tmp_paths, "@indep-probe",
    )

    assert out_empty_inbox == out_full_inbox


def test_capsule_multi_project_each_has_pointer(tmp_paths: Paths):
    """Each declared project gets its own file-path pointer in the
    capsule output."""
    _seed_root_agent_mission(
        tmp_paths, "@multi", frontmatter="projects: [alpha, beta]\n",
    )
    _seed_project_file(
        tmp_paths, "alpha", "LEARNINGS.md",
        "## 2026-05-29: a\n\nbody alpha\n",
    )
    _seed_project_file(
        tmp_paths, "beta", "LEARNINGS.md",
        "## 2026-05-29: b\n\nbody beta\n",
    )
    out = ctx._render_project_capsule(tmp_paths, "@multi")

    alpha_path = tmp_paths.projects / "alpha" / "LEARNINGS.md"
    beta_path = tmp_paths.projects / "beta" / "LEARNINGS.md"
    assert "## Project: alpha" in out
    assert "## Project: beta" in out
    assert str(alpha_path) in out
    assert str(beta_path) in out


def test_capsule_b4_path_inference_regression(tmp_paths: Paths):
    """B4 path-inference still hits the per-project file under the
    recency render path."""
    _seed_project_nested_agent_mission(
        tmp_paths, "widget", "@b4-probe",
    )
    _seed_project_file(
        tmp_paths, "widget", "LEARNINGS.md",
        "## 2026-05-29: hetzner tunnel\n\ntunnel content body\n",
    )
    out = ctx._render_project_capsule(tmp_paths, "@b4-probe")
    assert "## Project: widget" in out
    assert "hetzner tunnel" in out
    assert "tunnel content body" in out


def test_capsule_t1_explicit_frontmatter_regression(tmp_paths: Paths):
    """T1 explicit ``project: <name>`` frontmatter still renders."""
    _seed_root_agent_mission(
        tmp_paths, "@t1-probe", frontmatter="project: widget\n",
    )
    _seed_project_file(
        tmp_paths, "widget", "LEARNINGS.md",
        "## 2026-05-29: entry\n\nbody\n",
    )
    out = ctx._render_project_capsule(tmp_paths, "@t1-probe")
    assert "## Project: widget" in out
    assert "entry" in out


def test_capsule_no_mission_no_inference_returns_empty(tmp_paths: Paths):
    """Graceful no-op when no MISSION.md AND no path-nesting AND name
    prefix doesn't match a project."""
    # No MISSION.md seeded for this agent.
    out = ctx._render_project_capsule(tmp_paths, "@orphan")
    assert out == ""


def test_capsule_recency_newest_first_e2e(tmp_paths: Paths):
    """End-to-end: oldest-to-newest entries in file → newest renders
    first in capsule output."""
    _seed_project_nested_agent_mission(
        tmp_paths, "widget", "@recency-probe",
    )
    content = (
        "## 2026-01-01: ancient\n\nancient body content\n\n"
        "## 2026-03-15: middle\n\nmiddle body content\n\n"
        "## 2026-05-29: newest\n\nnewest body content\n"
    )
    _seed_project_file(
        tmp_paths, "widget", "LEARNINGS.md", content,
    )
    out = ctx._render_project_capsule(tmp_paths, "@recency-probe")
    newest_idx = out.find("2026-05-29: newest")
    middle_idx = out.find("2026-03-15: middle")
    ancient_idx = out.find("2026-01-01: ancient")
    assert newest_idx >= 0
    assert middle_idx > newest_idx
    assert ancient_idx > middle_idx
