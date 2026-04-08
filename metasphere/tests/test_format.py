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


def test_format_task_table_contains_headers_and_rows(tmp_paths, monkeypatch):
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@alice")
    a = _tasks.create_task("alpha", "!high", tmp_paths.scope, tmp_paths.repo,
                           project="recurse", assigned_to="@alice")
    b = _tasks.create_task("beta", "!normal", tmp_paths.scope, tmp_paths.repo,
                           project="default", assigned_to="@bob")
    table = fmt.format_task_table([a, b])
    assert "TITLE" in table and "OWNER" in table and "PROJECT" in table
    assert "alpha" in table and "beta" in table
    assert "@alice" in table and "@bob" in table
    assert "recurse" in table
    assert "🔵" in table  # both pending
    # header rule present
    assert "-+-" in table


def test_format_schedule_table():
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
    table = fmt.format_schedule_table([j1, j2])
    assert "NAME" in table and "EXPR" in table and "LAST FIRED" in table
    assert "daily digest" in table
    assert "🟢" in table and "🔴" in table
    assert "2021-01-01 00:00" in table


def test_plain_mode_respects_env(monkeypatch):
    monkeypatch.setenv("METASPHERE_PLAIN", "1")
    assert fmt.is_plain_mode() is True
    monkeypatch.delenv("METASPHERE_PLAIN")
    monkeypatch.setenv("NO_COLOR", "1")
    assert fmt.is_plain_mode() is True
