"""Tests for metasphere.heartbeat."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from metasphere import heartbeat as hb
from metasphere.messages import send_message
from metasphere.paths import Paths
from metasphere.tasks import create_task


def _agent(paths: Paths, name: str, status: str) -> Path:
    d = paths.agents / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "status").write_text(status, encoding="utf-8")
    (d / "scope").write_text(str(paths.project_root), encoding="utf-8")
    return d


# ---------------------------------------------------------------------------
# check_urgent_messages
# ---------------------------------------------------------------------------


def test_check_urgent_messages_finds_unread_urgent(tmp_paths: Paths):
    send_message("@.", "!urgent", "fire", from_agent="@user", paths=tmp_paths, wake=False)
    send_message("@.", "!info", "calm", from_agent="@user", paths=tmp_paths, wake=False)
    found = hb.check_urgent_messages(tmp_paths)
    assert len(found) == 1
    assert found[0].label == "!urgent"
    assert found[0].body.strip() == "fire"


# ---------------------------------------------------------------------------
# check_blocked_agents
# ---------------------------------------------------------------------------


def test_check_blocked_agents_finds_waiting_and_blocked(tmp_paths: Paths):
    _agent(tmp_paths, "@a", "waiting: input")
    _agent(tmp_paths, "@b", "blocked: dep")
    _agent(tmp_paths, "@c", "active: working")
    found = {a.name for a in hb.check_blocked_agents(tmp_paths)}
    assert found == {"@a", "@b"}


# ---------------------------------------------------------------------------
# check_urgent_tasks
# ---------------------------------------------------------------------------


def test_check_urgent_tasks_counts_correctly(tmp_paths: Paths):
    create_task("urgent one", "!urgent", tmp_paths.scope, tmp_paths.project_root)
    create_task("normal one", "!normal", tmp_paths.scope, tmp_paths.project_root)
    create_task("urgent two", "!urgent", tmp_paths.scope, tmp_paths.project_root)
    urgent, total = hb.check_urgent_tasks(tmp_paths)
    assert urgent == 2
    assert total == 3


# ---------------------------------------------------------------------------
# build_agent_context
# ---------------------------------------------------------------------------


def test_build_agent_context_prepends_header(tmp_paths: Paths):
    out = hb.build_agent_context("@orchestrator", tmp_paths)
    assert out.startswith("# HEARTBEAT")
    # Sections from build_context still present.
    assert "Metasphere Delta" in out
    assert "Messages" in out
    assert "Tasks" in out


# ---------------------------------------------------------------------------
# heartbeat_once dedupes via the state file
# ---------------------------------------------------------------------------


def test_heartbeat_once_dedupes_urgent_messages(tmp_paths: Paths):
    msg = send_message(
        "@.", "!urgent", "boom", from_agent="@user", paths=tmp_paths, wake=False
    )

    events: list[tuple] = []
    real = hb.log_event

    def fake_log(*args, **kwargs):
        events.append((args, kwargs))
        return real(*args, **kwargs)

    with mock.patch.object(hb, "log_event", side_effect=fake_log):
        hb.heartbeat_once(tmp_paths)
        first_calls = [
            e for e in events if e[0] and e[0][0] == "heartbeat.urgent_message"
        ]
        hb.heartbeat_once(tmp_paths)
        second_calls = [
            e for e in events if e[0] and e[0][0] == "heartbeat.urgent_message"
        ]

    assert len(first_calls) == 1
    # No new urgent_message log on the second tick — deduped.
    assert len(second_calls) == 1
    assert hb.already_notified(tmp_paths, f"urgent:{msg.id}")


# ---------------------------------------------------------------------------
# invoke_agent_heartbeat falls back to one-shot when no tmux session
# ---------------------------------------------------------------------------


def test_invoke_agent_heartbeat_falls_back_to_oneshot(tmp_paths: Paths):
    _agent(tmp_paths, "@orchestrator", "active")

    with mock.patch.object(hb, "session_alive", return_value=False), mock.patch.object(
        hb.subprocess, "run"
    ) as run:
        ok = hb.invoke_agent_heartbeat("@orchestrator", tmp_paths)

    assert ok is True
    assert run.called
    args, kwargs = run.call_args
    cmd = args[0]
    assert cmd[0] == "claude"
    assert "-p" in cmd
    assert "--allowedTools" in cmd


def test_invoke_agent_heartbeat_passes_defer_if_busy_true(tmp_paths: Paths):
    """Heartbeat is a NON-user auto-injector: it must pass
    ``defer_if_busy=True`` so it backs off when the REPL pane shows
    typed content (the 2026-04-16 'heartbeat took over my cursor'
    bug). Companion to the telegram-handler test that asserts the
    USER inbound path passes False.
    """
    _agent(tmp_paths, "@orchestrator", "active")

    captured: list[dict] = []

    def fake_submit(session, message, **kwargs):
        captured.append({"session": session, "kwargs": dict(kwargs)})
        return True

    with mock.patch.object(hb, "session_alive", return_value=True), \
         mock.patch("metasphere.tmux.submit_to_tmux", fake_submit):
        ok = hb.invoke_agent_heartbeat("@orchestrator", tmp_paths)

    assert ok is True
    assert len(captured) == 1
    assert captured[0]["kwargs"].get("defer_if_busy") is True, (
        "heartbeat (non-user auto-injector) must defer when the REPL "
        "buffer shows typed content"
    )


def test_log_status_to_disk_writes_marker(tmp_paths: Paths):
    hb.log_status_to_disk(tmp_paths)
    p = tmp_paths.state / "heartbeat_last_run"
    assert p.is_file()
    assert "alive at" in p.read_text(encoding="utf-8")


def test_invoke_agent_heartbeat_uses_project_scoped_session(tmp_paths: Paths):
    """Regression: project-scoped persistent agents (research-monitors,
    etc.) live in ``metasphere-<project>-<agent>`` sessions. Bare
    ``session_name_for`` would target ``metasphere-<agent>``, miss the
    real session, and silently fall through to the ``claude -p``
    one-shot path — the persistent session never receives heartbeat
    pastes. Sister-fix to the posthook deferred-cmd resolution bug.
    """
    from metasphere.agents import AgentRecord

    _agent(tmp_paths, "@brand-mentions", "active")
    rec = AgentRecord(
        name="@brand-mentions",
        scope="",
        parent="",
        status="",
        spawned_at="",
        project="research",
    )

    captured: list[str] = []

    def fake_submit(session, message, **kwargs):
        captured.append(session)
        return True

    with mock.patch("metasphere.session.list_agents", return_value=[rec]), \
         mock.patch.object(hb, "session_alive", return_value=True), \
         mock.patch("metasphere.tmux.submit_to_tmux", fake_submit):
        ok = hb.invoke_agent_heartbeat("@brand-mentions", tmp_paths)

    assert ok is True
    assert captured == ["metasphere-research-brand-mentions"], (
        f"expected project-aware session name, got {captured!r}"
    )


# ---------------------------------------------------------------------------
# scope normalization in daemon path
# ---------------------------------------------------------------------------


def test_heartbeat_daemon_normalizes_scope_to_repo(tmp_paths: Paths, monkeypatch):
    """Daemon must use paths.project_root (env-resolved) not the cwd subdir.

    Simulates running the daemon from a deeply nested ``a/b/c`` subdir
    of the repo and asserts the per-tick :class:`Paths` carries the
    repo root, not the cwd.
    """
    nested = tmp_paths.project_root / "a" / "b" / "c"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    captured: list[Paths] = []

    def fake_once(paths, invoke_agent=False):
        captured.append(paths)

    def fake_sleep(_seconds):
        raise StopIteration  # break out after one tick

    monkeypatch.setattr(hb, "heartbeat_once", fake_once)
    monkeypatch.setattr(hb.time, "sleep", fake_sleep)

    with pytest.raises(StopIteration):
        hb.heartbeat_daemon(interval_seconds=0)

    assert len(captured) == 1
    p = captured[0]
    assert p.project_root == tmp_paths.project_root
    assert p.project_root != nested


# ---------------------------------------------------------------------------
# QUESTIONS.md reminder
# ---------------------------------------------------------------------------


import datetime as _dt


_QUESTIONS_SAMPLE = """\
# QUESTIONS.md — what Spot needs from the operator

Legend: 🔴 blocking · 🟡 soon · 🟢 FYI.

## mesa.chat
- 🔴 PR #40 vault privacy fix: awaiting OK to merge to prod. (2026-06-21)
- ✅ RESOLVED: contact form testable as-is. (2026-06-21)
- 🟢 TCK-5 platform-fee: defer to after launch? (2026-06-21)

## widget
- 🟡 the partner's API design sketches: where do they live? (2026-06-21)
"""


def _write_questions(paths: Paths, text: str) -> None:
    paths.state.mkdir(parents=True, exist_ok=True)
    (paths.state / "QUESTIONS.md").write_text(text, encoding="utf-8")


def test_parse_questions_extracts_flag_project_and_date():
    items = hb.parse_questions(_QUESTIONS_SAMPLE)
    # Only the three flagged bullets; the ✅ resolved line is skipped.
    assert len(items) == 3
    red = [q for q in items if q.flag == "🔴"]
    assert len(red) == 1
    assert red[0].project == "mesa.chat"
    assert "PR #40" in red[0].text
    assert red[0].raised == _dt.date(2026, 6, 21)
    widget = [q for q in items if q.project == "widget"]
    assert widget and widget[0].flag == "🟡"


def test_check_questions_red_always_green_never(tmp_paths: Paths):
    _write_questions(tmp_paths, _QUESTIONS_SAMPLE)
    # Same day raised → amber NOT yet aged past 24h default.
    due = hb.check_questions(tmp_paths, today=_dt.date(2026, 6, 21))
    flags = sorted(q.flag for q in due)
    assert flags == ["🔴"], "fresh amber not due, green never, red always"


def test_check_questions_amber_ages_in(tmp_paths: Paths):
    _write_questions(tmp_paths, _QUESTIONS_SAMPLE)
    # Two days later the amber crosses the 24h threshold.
    due = hb.check_questions(tmp_paths, today=_dt.date(2026, 6, 23))
    flags = sorted(q.flag for q in due)
    assert flags == ["🔴", "🟡"]


def test_check_questions_missing_file_is_empty(tmp_paths: Paths):
    assert hb.check_questions(tmp_paths) == []


def test_format_questions_ping_batches_into_one_message():
    items = hb.parse_questions(_QUESTIONS_SAMPLE)
    msg = hb._format_questions_ping(items)
    assert msg.count("🔴") == 1
    assert "blocking" in msg
    # One header line + one line per item, never one message per item.
    assert msg.startswith("⏳ Needs from the operator")


def test_heartbeat_once_skips_questions_when_disabled(tmp_paths: Paths, monkeypatch):
    _write_questions(tmp_paths, _QUESTIONS_SAMPLE)
    monkeypatch.delenv("METASPHERE_QUESTIONS_ENABLED", raising=False)
    sent: list[str] = []
    monkeypatch.setattr(hb, "_notify_user", lambda text, paths: sent.append(text))
    hb.heartbeat_once(tmp_paths)
    assert not any("Needs from the operator" in s for s in sent)


def test_heartbeat_once_pings_and_dedupes_when_enabled(tmp_paths: Paths, monkeypatch):
    _write_questions(tmp_paths, _QUESTIONS_SAMPLE)
    monkeypatch.setenv("METASPHERE_QUESTIONS_ENABLED", "1")
    # Force work-hours + a stable cooldown bucket regardless of wall clock.
    monkeypatch.setattr(hb, "_questions_in_work_hours", lambda now=None: True)
    monkeypatch.setattr(hb, "_cooldown_bucket", lambda now=None: 12345)
    sent: list[str] = []
    monkeypatch.setattr(hb, "_notify_user", lambda text, paths: sent.append(text))

    hb.heartbeat_once(tmp_paths)
    hb.heartbeat_once(tmp_paths)  # same due-set + bucket → deduped

    pings = [s for s in sent if "Needs from the operator" in s]
    assert len(pings) == 1, "same due-set in one cooldown window must ping once"


def test_questions_work_hours_window(monkeypatch):
    monkeypatch.setenv("METASPHERE_QUESTIONS_TZ", "UTC")
    monkeypatch.setenv("METASPHERE_QUESTIONS_WORK_START", "11")
    monkeypatch.setenv("METASPHERE_QUESTIONS_WORK_END", "22")
    inside = _dt.datetime(2026, 6, 29, 14, 0, tzinfo=_dt.timezone.utc)
    outside = _dt.datetime(2026, 6, 29, 23, 0, tzinfo=_dt.timezone.utc)
    assert hb._questions_in_work_hours(inside) is True
    assert hb._questions_in_work_hours(outside) is False


# ---------------------------------------------------------------------------
# Intake-drift safeguard (weekly-plan "Needs from the operator" → QUESTIONS.md)
# ---------------------------------------------------------------------------


_WEEKLY_PLAN_SAMPLE = """\
# Weekly plan — widget

## Needs from the operator

- 🔴 the partner's API design sketches: where do they live? (2026-06-21)
- Greenlight the qdrant migration window.

## This week

- Sequence after the live partitioning migration.
"""

_QUESTIONS_WITH_ONE_PROMOTED = """\
# QUESTIONS.md

## widget
- 🟡 the partner's API design sketches — where do they live? (2026-06-21)
"""


def _write_weekly_plan(paths: Paths, project: str, text: str) -> None:
    paths.state.mkdir(parents=True, exist_ok=True)
    (paths.state / f"weekly-plan-{project}.md").write_text(text, encoding="utf-8")


def test_parse_needs_from_operator_only_section_bullets():
    items = hb.parse_needs_from_operator(_WEEKLY_PLAN_SAMPLE)
    # Two bullets under the heading; flag stripped; "This week" excluded.
    assert len(items) == 2
    assert items[0].startswith("the partner's API design sketches")
    assert "Greenlight" in items[1]


def test_parse_needs_from_operator_skips_placeholder():
    text = "## Needs from the operator\n\n_(none currently recorded — lead to maintain)_\n\n## This week\n- x\n"
    assert hb.parse_needs_from_operator(text) == []


def test_check_intake_drift_reports_unpromoted_only(tmp_paths: Paths):
    _write_weekly_plan(tmp_paths, "widget", _WEEKLY_PLAN_SAMPLE)
    _write_questions(tmp_paths, _QUESTIONS_WITH_ONE_PROMOTED)
    drift = hb.check_intake_drift(tmp_paths)
    # The API-sketches item IS in QUESTIONS.md (fuzzy-matched despite the
    # reworded dash/flag) → only the greenlight item drifts.
    assert len(drift) == 1
    assert drift[0].project == "widget"
    assert "Greenlight" in drift[0].text


def test_check_intake_drift_empty_when_all_promoted(tmp_paths: Paths):
    _write_weekly_plan(
        tmp_paths,
        "widget",
        "## Needs from the operator\n- 🔴 the partner's API design sketches: where do they live? (2026-06-21)\n",
    )
    _write_questions(tmp_paths, _QUESTIONS_WITH_ONE_PROMOTED)
    assert hb.check_intake_drift(tmp_paths) == []


def test_check_intake_drift_archive_does_not_alarm(tmp_paths: Paths):
    # Archived plans live one level down — the non-recursive glob skips them.
    archive = tmp_paths.state / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    (archive / "weekly-plan-widget.md").write_text(
        _WEEKLY_PLAN_SAMPLE, encoding="utf-8"
    )
    assert hb.check_intake_drift(tmp_paths) == []


def test_check_intake_drift_no_questions_file_all_drift(tmp_paths: Paths):
    _write_weekly_plan(tmp_paths, "widget", _WEEKLY_PLAN_SAMPLE)
    drift = hb.check_intake_drift(tmp_paths)
    assert len(drift) == 2  # nothing promoted yet → both bullets drift


def test_heartbeat_once_skips_intake_drift_when_disabled(tmp_paths: Paths, monkeypatch):
    _write_weekly_plan(tmp_paths, "widget", _WEEKLY_PLAN_SAMPLE)
    monkeypatch.delenv("METASPHERE_QUESTIONS_INTAKE_DRIFT_ENABLED", raising=False)
    sent: list[tuple] = []
    monkeypatch.setattr(hb, "send_message", lambda *a, **k: sent.append((a, k)))
    hb.heartbeat_once(tmp_paths)
    assert sent == []


def test_heartbeat_once_intake_drift_to_orchestrator_and_dedupes(
    tmp_paths: Paths, monkeypatch
):
    _write_weekly_plan(tmp_paths, "widget", _WEEKLY_PLAN_SAMPLE)
    monkeypatch.setenv("METASPHERE_QUESTIONS_INTAKE_DRIFT_ENABLED", "1")
    monkeypatch.setattr(hb, "_cooldown_bucket", lambda now=None: 54321)
    # Intake drift must NOT ping the operator.
    operator: list[str] = []
    monkeypatch.setattr(hb, "_notify_user", lambda text, paths: operator.append(text))
    sent: list[tuple] = []
    monkeypatch.setattr(hb, "send_message", lambda *a, **k: sent.append((a, k)))

    hb.heartbeat_once(tmp_paths)
    hb.heartbeat_once(tmp_paths)  # same drift-set + bucket → deduped

    assert len(sent) == 1, "same drift-set in one cooldown window must send once"
    args, kwargs = sent[0]
    assert args[0] == "@orchestrator"
    assert args[1] == "!info"
    assert "Intake pending" in args[2]
    assert kwargs.get("wake") is False
    assert not any("Intake pending" in t for t in operator), "must not ping the operator"
