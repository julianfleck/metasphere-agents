"""Tests for ``metasphere questions`` — the QUESTIONS.md read view."""

from __future__ import annotations

import datetime as _dt
import os

from metasphere.cli import questions as q
from metasphere.paths import Paths


_SAMPLE = """\
# QUESTIONS.md — what Spot needs from the operator

Legend: 🔴 blocking · 🟡 soon · 🟢 FYI.

## mesa.chat
- 🔴 PR #40 vault privacy fix: awaiting OK to merge to prod. (2026-06-21)
- ✅ RESOLVED: contact form testable as-is. (2026-06-21)
- 🟢 TCK-5 platform-fee: defer to after launch? (2026-06-21)

## widget
- 🟡 the partner's API design sketches: where do they live? (2026-06-21)
"""


def _write(paths: Paths, text: str) -> None:
    paths.state.mkdir(parents=True, exist_ok=True)
    (paths.state / "QUESTIONS.md").write_text(text, encoding="utf-8")


def test_render_all_groups_by_project_and_counts(tmp_paths: Paths):
    _write(tmp_paths, _SAMPLE)
    body, rc = q.render_questions()
    assert rc == 0
    # Header counts every live flagged item (the ✅ line is skipped).
    assert "1 🔴 blocking" in body
    assert "1 🟡 soon" in body
    assert "1 🟢 fyi" in body
    # Grouped under project headings.
    assert "## mesa.chat" in body
    assert "## widget" in body
    # The resolved (✅) bullet never appears.
    assert "RESOLVED" not in body
    assert "PR #40" in body


def test_filter_red_only(tmp_paths: Paths):
    _write(tmp_paths, _SAMPLE)
    body, rc = q.render_questions("red")
    assert rc == 0
    assert "PR #40" in body
    assert "🟡" not in body
    assert "🟢" not in body


def test_filter_open_excludes_green(tmp_paths: Paths):
    _write(tmp_paths, _SAMPLE)
    body, rc = q.render_questions("open")
    assert rc == 0
    assert "🔴" in body
    assert "🟡" in body
    assert "🟢" not in body


def test_unknown_filter_degrades_to_all(tmp_paths: Paths):
    _write(tmp_paths, _SAMPLE)
    body, rc = q.render_questions("bogus")
    assert rc == 0
    # Treated as no filter → everything renders.
    assert "🔴" in body and "🟡" in body and "🟢" in body


def test_missing_file_is_clean_rc0(tmp_paths: Paths):
    body, rc = q.render_questions()
    assert rc == 0
    assert "not found" in body.lower()


def test_empty_after_filter(tmp_paths: Paths):
    _write(tmp_paths, "## proj\n- 🟢 only an fyi (2026-06-21)\n")
    body, rc = q.render_questions("red")
    assert rc == 0
    assert "no matching items" in body.lower()


def test_main_help(capsys):
    rc = q.main(["--help"])
    out, _ = capsys.readouterr()
    assert rc == 0
    assert "metasphere questions" in out


def test_main_prints_body(tmp_paths: Paths, capsys):
    _write(tmp_paths, _SAMPLE)
    rc = q.main([])
    out, _ = capsys.readouterr()
    assert rc == 0
    assert "Needs from the operator" in out


def test_telegram_cmd_questions(tmp_paths: Paths):
    """The /questions telegram command renders the same body."""
    _write(tmp_paths, _SAMPLE)
    from metasphere.telegram.commands import cmd_questions, Context

    ctx = Context(chat_id=1, from_user="alice")
    body = cmd_questions("", ctx)
    assert "Needs from the operator" in body
    body_red = cmd_questions("red", ctx)
    assert "PR #40" in body_red
    assert "🟢" not in body_red


# ---------------------------------------------------------------------------
# Staleness note — surfaces an unmaintained ledger instead of presenting
# long-resolved items as live (proposal questions-md-wiring; mtime proxy).
# ---------------------------------------------------------------------------


def _backdate(paths: Paths, days: float) -> None:
    p = paths.state / "QUESTIONS.md"
    old = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days)).timestamp()
    os.utime(p, (old, old))


def test_fresh_ledger_has_no_staleness_note(tmp_paths: Paths):
    _write(tmp_paths, _SAMPLE)  # mtime ~now
    body, rc = q.render_questions()
    assert rc == 0
    assert "stale" not in body.lower()


def test_stale_ledger_warns_in_header(tmp_paths: Paths):
    _write(tmp_paths, _SAMPLE)
    _backdate(tmp_paths, 9)
    body, rc = q.render_questions()
    assert rc == 0
    assert "may be stale" in body
    # The warning precedes the items, not buried after them.
    assert body.index("may be stale") < body.index("## mesa.chat")
    # Items still render normally alongside the warning.
    assert "PR #40" in body


def test_stale_note_also_shows_when_filter_empties_list(tmp_paths: Paths):
    # A stale ledger with no items matching the filter still surfaces the
    # warning — the "nothing matched" line shouldn't hide that it's stale.
    _write(tmp_paths, "# QUESTIONS.md\n\n## x\n- 🟢 fyi only (2026-06-01)\n")
    _backdate(tmp_paths, 9)
    body, rc = q.render_questions("red")
    assert rc == 0
    assert "may be stale" in body


def test_staleness_threshold_env_override(tmp_paths: Paths, monkeypatch):
    _write(tmp_paths, _SAMPLE)
    _backdate(tmp_paths, 3)
    # Default 7d → 3d-old file is fresh.
    assert "may be stale" not in q.render_questions()[0]
    # Tighten to 2d → now flagged.
    monkeypatch.setenv("METASPHERE_QUESTIONS_STALE_DAYS", "2")
    assert "may be stale" in q.render_questions()[0]
    # Zero/negative disables the check entirely.
    monkeypatch.setenv("METASPHERE_QUESTIONS_STALE_DAYS", "0")
    assert "may be stale" not in q.render_questions()[0]
