"""Tests for memory-index lint — overlong lines, dead links, orphan files."""

from __future__ import annotations

from pathlib import Path

from metasphere.memory.lint import DEFAULT_MAX_LINE, lint_index, render


def _write_index(tmp_path: Path, body: str) -> Path:
    root = tmp_path / "memory"
    root.mkdir()
    (root / "MEMORY.md").write_text(body, encoding="utf-8")
    return root


def test_clean_index_is_ok(tmp_path):
    root = _write_index(
        tmp_path,
        "# Memory Index\n\n- [Short entry](real.md) — a tidy one-line hook\n",
    )
    (root / "real.md").write_text("body", encoding="utf-8")
    report = lint_index(root)
    assert report.ok
    assert report.index_entries == 1
    assert report.long_lines == []
    assert report.dead_links == []


def test_overlong_line_flagged(tmp_path):
    long_hook = "x" * 300
    root = _write_index(tmp_path, f"- [Title](real.md) — {long_hook}\n")
    (root / "real.md").write_text("body", encoding="utf-8")
    report = lint_index(root)
    assert not report.ok
    assert len(report.long_lines) == 1
    assert report.long_lines[0].lineno == 1
    assert report.long_lines[0].length > DEFAULT_MAX_LINE


def test_max_line_threshold_is_configurable(tmp_path):
    # A line ~210 chars: over the 200 default, under a 300 ceiling.
    line = "- [T](real.md) — " + "x" * 200
    root = _write_index(tmp_path, line + "\n")
    (root / "real.md").write_text("body", encoding="utf-8")
    assert not lint_index(root, max_line=200).ok
    assert lint_index(root, max_line=300).ok


def test_dead_link_flagged(tmp_path):
    root = _write_index(tmp_path, "- [Gone](missing.md) — points nowhere\n")
    report = lint_index(root)
    assert not report.ok
    assert len(report.dead_links) == 1
    assert report.dead_links[0].rel_path == "missing.md"
    assert report.dead_links[0].title == "Gone"


def test_path_traversal_link_flagged_as_dead(tmp_path):
    # A link escaping the memory root must never resolve — it is treated
    # as dead (same containment guard AutoMemoryStrategy enforces).
    root = _write_index(tmp_path, "- [Escape](../../../etc/passwd.md) — nope\n")
    report = lint_index(root)
    assert not report.ok
    assert any("passwd" in dl.rel_path for dl in report.dead_links)


def test_non_index_lines_ignored(tmp_path):
    # Headings and prose without a (file.md) link are not index entries,
    # so their length never trips the line check.
    prose = "This is a long paragraph of narration " + "y" * 300
    root = _write_index(tmp_path, f"# Heading\n\n{prose}\n")
    report = lint_index(root)
    assert report.ok
    assert report.index_entries == 0


def test_missing_memory_md(tmp_path):
    report = lint_index(tmp_path)  # no MEMORY.md created
    assert not report.exists
    assert not report.ok
    assert "not found" in render(report)


def test_reports_size_and_line_count(tmp_path):
    root = _write_index(
        tmp_path,
        "# Index\n- [A](a.md) — one\n- [B](b.md) — two\n",
    )
    (root / "a.md").write_text("x", encoding="utf-8")
    (root / "b.md").write_text("x", encoding="utf-8")
    report = lint_index(root)
    assert report.line_count == 3
    assert report.index_entries == 2
    assert report.size_bytes > 0


def test_render_lists_violations(tmp_path):
    root = _write_index(
        tmp_path,
        f"- [Fat]({'real.md'}) — {'z' * 300}\n- [Gone](missing.md) — x\n",
    )
    (root / "real.md").write_text("x", encoding="utf-8")
    text = render(lint_index(root))
    assert "overlong" in text
    assert "dead link" in text
    assert "clean" not in text


def test_lint_never_mutates_index(tmp_path):
    body = f"- [Fat](real.md) — {'z' * 300}\n"
    root = _write_index(tmp_path, body)
    (root / "real.md").write_text("x", encoding="utf-8")
    lint_index(root)
    # report-only: the file is untouched after linting
    assert (root / "MEMORY.md").read_text(encoding="utf-8") == body


def _topic(text: str = "the fact") -> str:
    """A minimal topic-memory file body with YAML frontmatter."""
    return f"---\nname: x\ndescription: y\nmetadata:\n  type: feedback\n---\n\n{text}\n"


def test_orphan_topic_file_flagged(tmp_path):
    # An indexed entry plus a topic file with frontmatter that NO entry names.
    root = _write_index(tmp_path, "- [Known](known.md) — indexed\n")
    (root / "known.md").write_text(_topic(), encoding="utf-8")
    (root / "stray.md").write_text(_topic(), encoding="utf-8")
    report = lint_index(root)
    assert not report.ok
    assert [o.rel_path for o in report.orphan_files] == ["stray.md"]
    assert "orphan" in render(report)


def test_indexed_topic_file_is_not_orphan(tmp_path):
    root = _write_index(tmp_path, "- [Known](known.md) — indexed\n")
    (root / "known.md").write_text(_topic(), encoding="utf-8")
    report = lint_index(root)
    assert report.ok
    assert report.orphan_files == []


def test_non_frontmatter_files_are_not_orphans(tmp_path):
    # Logs / notes without frontmatter (e.g. night-log.md) are not topic
    # memories, so they are never expected in the index.
    root = _write_index(tmp_path, "# Index\n")
    (root / "night-log.md").write_text("2026-06-23 some log line\n", encoding="utf-8")
    (root / "notes.md").write_text("plain notes, no frontmatter\n", encoding="utf-8")
    report = lint_index(root)
    assert report.orphan_files == []
    assert report.ok


def test_orphan_check_tolerates_leading_blank_lines(tmp_path):
    root = _write_index(tmp_path, "# Index\n")
    (root / "spaced.md").write_text("\n\n" + _topic(), encoding="utf-8")
    report = lint_index(root)
    assert [o.rel_path for o in report.orphan_files] == ["spaced.md"]


def test_indexed_entry_with_bracketed_title_is_not_orphan(tmp_path):
    # A title carrying nested brackets defeats the link regex's title group,
    # but the parenthesised target still anchors the file as referenced — so
    # it must not be misreported as an orphan (regression for that case).
    root = _write_index(
        tmp_path,
        "- [Use `[idle]` sigil, not [ack]](bracketed.md) — quiet heartbeats\n",
    )
    (root / "bracketed.md").write_text(_topic(), encoding="utf-8")
    report = lint_index(root)
    assert report.orphan_files == []


def test_short_filename_not_matched_inside_longer_link(tmp_path):
    # ``(a.md)`` must not be considered referenced by ``(extra.md)``.
    root = _write_index(tmp_path, "- [Long](extra.md) — only this is indexed\n")
    (root / "extra.md").write_text(_topic(), encoding="utf-8")
    (root / "a.md").write_text(_topic(), encoding="utf-8")
    report = lint_index(root)
    assert [o.rel_path for o in report.orphan_files] == ["a.md"]


def _topic_desc(description: str, *, quote: str = "") -> str:
    """A topic-file body whose frontmatter carries a chosen description."""
    return (
        f"---\nname: t\ndescription: {quote}{description}{quote}\n"
        "metadata:\n  type: feedback\n---\n\nbody\n"
    )


def test_suggest_replaces_bloated_title_with_description(tmp_path):
    # The whole fact jammed into the link title is the dominant bloat shape;
    # the suggestion swaps it for the topic file's canonical one-line
    # description while preserving the link target and the trailing hook.
    bloat = "X" * 250
    tail = " — 2026-06-23. See [[other]]"
    root = _write_index(tmp_path, f"- [{bloat}](fact.md){tail}\n")
    (root / "fact.md").write_text(_topic_desc("short canonical summary"), encoding="utf-8")

    report = lint_index(root, suggest=True)
    assert len(report.long_lines) == 1
    ll = report.long_lines[0]
    assert ll.suggestion == f"- [short canonical summary](fact.md){tail}"
    assert ll.suggestion_length == len(ll.suggestion)
    assert ll.suggestion_length < ll.length


def test_suggest_is_off_by_default(tmp_path):
    root = _write_index(tmp_path, f"- [{'X' * 250}](fact.md) — t\n")
    (root / "fact.md").write_text(_topic_desc("summary"), encoding="utf-8")
    report = lint_index(root)  # suggest defaults to False
    assert report.long_lines[0].suggestion is None


def test_suggest_strips_quotes_from_description(tmp_path):
    root = _write_index(tmp_path, f"- [{'X' * 250}](fact.md) — t\n")
    (root / "fact.md").write_text(
        _topic_desc("quoted summary", quote='"'), encoding="utf-8"
    )
    report = lint_index(root, suggest=True)
    assert report.long_lines[0].suggestion == '- [quoted summary](fact.md) — t'


def test_suggest_none_when_no_description(tmp_path):
    # Frontmatter without a description gives nothing to compress toward.
    root = _write_index(tmp_path, f"- [{'X' * 250}](fact.md) — t\n")
    (root / "fact.md").write_text(
        "---\nname: t\nmetadata:\n  type: feedback\n---\n\nbody\n", encoding="utf-8"
    )
    report = lint_index(root, suggest=True)
    assert report.long_lines[0].suggestion is None


def test_suggest_none_for_dead_link_target(tmp_path):
    # A dead link has no file to read a description from — no crash, no suggestion.
    root = _write_index(tmp_path, f"- [{'X' * 250}](gone.md) — t\n")
    report = lint_index(root, suggest=True)
    assert report.long_lines[0].suggestion is None


def test_render_shows_suggestion_and_still_over_flag(tmp_path):
    # When swapping the title still leaves the line over max-line (tail bloat),
    # render must surface the suggestion AND honestly flag it as still over.
    long_tail = " — " + "y" * 250
    root = _write_index(tmp_path, f"- [{'X' * 50}](fact.md){long_tail}\n")
    (root / "fact.md").write_text(_topic_desc("tiny"), encoding="utf-8")
    report = lint_index(root, suggest=True)
    text = render(report)
    assert "↳ suggest" in text
    assert "still over" in text


# --- apply_suggestions ------------------------------------------------------

from metasphere.memory.lint import apply_suggestions, render_apply  # noqa: E402


def test_apply_rewrites_title_bloated_line(tmp_path):
    # A line whose bloat is in the title is rewritten in place to the
    # shorter description-swapped form; the file shrinks.
    bloat = "X" * 250
    tail = " — 2026-06-23. See [[other]]"
    root = _write_index(tmp_path, f"- [{bloat}](fact.md){tail}\n")
    (root / "fact.md").write_text(_topic_desc("short canonical summary"), encoding="utf-8")
    before = (root / "MEMORY.md").read_text(encoding="utf-8")

    result = apply_suggestions(root)

    assert result.applied == 1
    assert result.skipped == 0
    after = (root / "MEMORY.md").read_text(encoding="utf-8")
    assert after != before
    assert "short canonical summary" in after
    assert "(fact.md)" in after  # link target preserved
    assert tail in after  # trailing hook preserved verbatim
    assert result.bytes_after < result.bytes_before


def test_apply_skips_when_swap_is_no_improvement(tmp_path):
    # Bloat lives in the tail and the title is already short, so swapping it
    # for a longer description would make the line worse — leave it verbatim.
    long_tail = " — " + "y" * 250
    body = f"- [Hi](fact.md){long_tail}\n"
    root = _write_index(tmp_path, body)
    (root / "fact.md").write_text(
        _topic_desc("a considerably longer description than the title"),
        encoding="utf-8",
    )

    result = apply_suggestions(root)

    assert result.applied == 0
    assert result.skipped == 1
    assert (root / "MEMORY.md").read_text(encoding="utf-8") == body


def test_apply_leaves_short_lines_untouched(tmp_path):
    body = "- [Fine](fact.md) — short\n"
    root = _write_index(tmp_path, body)
    (root / "fact.md").write_text(_topic_desc("desc"), encoding="utf-8")

    result = apply_suggestions(root)

    assert result.applied == 0
    assert (root / "MEMORY.md").read_text(encoding="utf-8") == body


def test_apply_preserves_entry_count_and_trailing_newline(tmp_path):
    bloat = "X" * 250
    body = (
        f"# Index\n\n- [{bloat}](a.md) — tail\n"
        f"- [Short](b.md) — ok\n- [{bloat}](c.md) — tail\n"
    )
    root = _write_index(tmp_path, body)
    for name in ("a", "b", "c"):
        (root / f"{name}.md").write_text(_topic_desc(f"summary {name}"), encoding="utf-8")

    result = apply_suggestions(root)

    after = (root / "MEMORY.md").read_text(encoding="utf-8")
    assert result.applied == 2
    assert after.endswith("\n")  # trailing newline round-trips
    # Entry count (lines carrying a (file.md) link) is unchanged.
    before_entries = sum(1 for ln in body.splitlines() if "](" in ln)
    after_entries = sum(1 for ln in after.splitlines() if "](" in ln)
    assert before_entries == after_entries == 3


def test_apply_no_write_when_nothing_to_do(tmp_path):
    body = "- [Fine](fact.md) — short\n"
    root = _write_index(tmp_path, body)
    (root / "fact.md").write_text(_topic_desc("desc"), encoding="utf-8")
    mtime_before = (root / "MEMORY.md").stat().st_mtime_ns

    result = apply_suggestions(root)

    assert result.applied == 0
    # Untouched file: not rewritten (mtime unchanged).
    assert (root / "MEMORY.md").stat().st_mtime_ns == mtime_before


def test_apply_missing_memory_md(tmp_path):
    result = apply_suggestions(tmp_path)  # no MEMORY.md
    assert result.exists is False
    assert result.applied == 0


def test_render_apply_reports_savings(tmp_path):
    bloat = "X" * 250
    root = _write_index(tmp_path, f"- [{bloat}](fact.md) — tail\n")
    (root / "fact.md").write_text(_topic_desc("short summary"), encoding="utf-8")
    result = apply_suggestions(root)
    text = render_apply(result)
    assert "applied 1" in text
    assert "saved" in text


# --- overlong frontmatter descriptions --------------------------------------


def test_overlong_description_flagged(tmp_path):
    # A topic file whose frontmatter description exceeds max_line is the root
    # cause of an index line --apply can never shrink: flag the file itself.
    # The index line here is short, so this is *additive* signal — the entry
    # is fine but the underlying file is bloated.
    root = _write_index(tmp_path, "- [Known](known.md) — indexed\n")
    (root / "known.md").write_text(_topic_desc("D" * 250), encoding="utf-8")
    report = lint_index(root)
    assert [ld.rel_path for ld in report.long_descriptions] == ["known.md"]
    assert report.long_descriptions[0].length == 250
    assert "overlong frontmatter description" in render(report)


def test_overlong_description_does_not_gate_ok(tmp_path):
    # Informational only: a bloated description alone (clean index line, no
    # dead links/orphans) must not flip the exit-code contract to red.
    root = _write_index(tmp_path, "- [Known](known.md) — indexed\n")
    (root / "known.md").write_text(_topic_desc("D" * 250), encoding="utf-8")
    report = lint_index(root)
    assert report.long_descriptions
    assert report.ok


def test_short_description_not_flagged(tmp_path):
    root = _write_index(tmp_path, "- [Known](known.md) — indexed\n")
    (root / "known.md").write_text(_topic_desc("a tidy one-line summary"), encoding="utf-8")
    report = lint_index(root)
    assert report.ok
    assert report.long_descriptions == []


def test_description_check_respects_max_line(tmp_path):
    # ~210-char description: over the 200 default, under a 300 ceiling.
    root = _write_index(tmp_path, "- [Known](known.md) — indexed\n")
    (root / "known.md").write_text(_topic_desc("d" * 210), encoding="utf-8")
    assert any(
        ld.rel_path == "known.md" for ld in lint_index(root, max_line=200).long_descriptions
    )
    assert lint_index(root, max_line=300).long_descriptions == []


def test_description_check_ignores_non_topic_files(tmp_path):
    # A log without frontmatter has no description to over-run, even if a
    # long line happens to start with "description:".
    root = _write_index(tmp_path, "# Index\n")
    (root / "night-log.md").write_text("description: " + "x" * 250 + "\n", encoding="utf-8")
    report = lint_index(root)
    assert report.long_descriptions == []
    assert report.ok
