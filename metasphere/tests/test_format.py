"""Tests for metasphere.format shared table renderer."""

from __future__ import annotations

from types import SimpleNamespace

from metasphere import format as fmt
from metasphere import tasks as _tasks


def test_fmt_iso_ts_basic():
    assert fmt.fmt_iso_ts("2026-04-08T11:44:22Z") == "2026-04-08 11:44"


def test_fmt_iso_ts_empty():
    assert fmt.fmt_iso_ts("") == "-"


def test_fmt_epoch_ts():
    # 2021-01-01 00:00:00 UTC
    assert fmt.fmt_epoch_ts(1609459200) == "2021-01-01 00:00"
    assert fmt.fmt_epoch_ts(0) == "-"


def test_ellipsize():
    assert fmt.ellipsize("hello", 10) == "hello"
    assert fmt.ellipsize("hello world", 8) == "hello w…"


def test_task_status_emoji_mapping():
    assert fmt.task_status_emoji("pending") == "🔵"
    assert fmt.task_status_emoji("in-progress") == "🟡"
    assert fmt.task_status_emoji("blocked") == "🔴"
    assert fmt.task_status_emoji("completed") == "🟢"
    assert fmt.task_status_emoji("stale") == "🟣"
    assert fmt.task_status_emoji("weird", assignee="") == "⚪"
    assert fmt.task_status_emoji("weird", assignee="@x") == "🔵"


def test_sched_status_emoji():
    assert fmt.sched_status_emoji(True) == "🟢"
    assert fmt.sched_status_emoji(False) == "🔴"


def test_format_task_cards_contains_fields(tmp_paths, monkeypatch):
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@alice")
    a = _tasks.create_task("alpha", "!high", tmp_paths.scope, tmp_paths.project_root,
                           project="recurse", assigned_to="@alice")
    b = _tasks.create_task("beta", "!normal", tmp_paths.scope, tmp_paths.project_root,
                           project="default", assigned_to="@bob")
    out = fmt.format_task_table([a, b])
    # Header
    assert out.splitlines()[0] == "Tasks"
    # Em-dash rule, not pipe-table
    assert "—" in out
    assert "|" not in out
    assert "alpha" in out and "beta" in out
    assert "@alice" in out and "@bob" in out
    assert "recurse" in out
    assert "🔵" in out  # both pending
    # Card metadata labels
    assert "Created:" in out and "Owner:" in out and "Project:" in out
    assert "Priority:" in out and "Status:" in out
    # No bold tags in plain mode
    assert "<b>" not in out


def test_format_task_cards_html_mode(tmp_paths, monkeypatch):
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@alice")
    a = _tasks.create_task("alpha & beta", "!high", tmp_paths.scope, tmp_paths.project_root,
                           project="recurse", assigned_to="@alice")
    out = fmt.format_task_table([a], html=True)
    assert "<b>Tasks</b>" in out
    assert "<b>alpha &amp; beta</b>" in out
    assert "<b>@alice</b>" in out
    assert "<b>recurse</b>" in out


def test_format_task_cards_title_truncation(tmp_paths, monkeypatch):
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@a")
    long_title = "x" * 80
    t = _tasks.create_task(long_title, "!normal", tmp_paths.scope, tmp_paths.project_root,
                           project="p", assigned_to="@a")
    out = fmt.format_task_table([t])
    assert "…" in out
    # truncated to TITLE_MAX, with ellipsis
    assert ("x" * 39 + "…") in out


def test_format_schedule_cards():
    j1 = SimpleNamespace(
        id="job-1", name="daily digest", agent_id="orchestrator",
        enabled=True, kind="cron", cron_expr="0 8 * * *", tz="UTC",
        last_fired_at=1609459200,
    )
    j2 = SimpleNamespace(
        id="job-2", name="offline", agent_id="@bot",
        enabled=False, kind="cron", cron_expr="*/5 * * * *", tz="UTC",
        last_fired_at=0,
    )
    out = fmt.format_schedule_table([j1, j2])
    assert out.splitlines()[0] == "Schedule"
    assert "—" in out
    assert "|" not in out
    assert "daily digest" in out
    assert "🟢" in out and "🔴" in out
    assert "2021-01-01 00:00" in out
    assert "Expression:" in out and "Last fired:" in out and "Next fire:" in out


def test_escape_html_basic():
    assert fmt.escape_html("a < b & c > d") == "a &lt; b &amp; c &gt; d"
    assert fmt.escape_html("") == ""
    # Ampersand must be escaped first to avoid double-escaping
    assert fmt.escape_html("&lt;") == "&amp;lt;"


def test_plain_mode_respects_env(monkeypatch):
    monkeypatch.setenv("METASPHERE_PLAIN", "1")
    assert fmt.is_plain_mode() is True
    monkeypatch.delenv("METASPHERE_PLAIN")
    monkeypatch.setenv("NO_COLOR", "1")
    assert fmt.is_plain_mode() is True
