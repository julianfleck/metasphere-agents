"""Tests for metasphere.schedule (port of scripts/metasphere-schedule)."""

from __future__ import annotations

import datetime as _dt
import time
from unittest import mock

import pytest

from metasphere import schedule as _sched
from metasphere.schedule import Job


def _make_job(**overrides) -> Job:
    base = dict(
        id="job-test-1",
        source="test",
        source_id="test-1",
        agent_id="main",
        name="research-monitor:brand-mentions",
        enabled=True,
        kind="cron",
        cron_expr="* * * * *",
        tz="UTC",
        payload_kind="agentTurn",
        payload_message="do the thing",
        model="anthropic/claude-sonnet-4-5",
        session_target="isolated",
        wake_mode="next-heartbeat",
        imported_at=1700000000,
        last_fired_at=0,
        next_run=0,
        command='send @main !task "x"',
        full_command="",
    )
    base.update(overrides)
    return Job(**base)


def test_load_save_roundtrip_preserves_all_fields(tmp_paths):
    j = _make_job()
    _sched.save_jobs([j], tmp_paths, _input_count=1)
    loaded = _sched.load_jobs(tmp_paths)
    assert len(loaded) == 1
    assert loaded[0] == j


def test_shrink_detection_refuses_zero_write(tmp_paths):
    j = _make_job()
    _sched.save_jobs([j], tmp_paths, _input_count=1)
    with pytest.raises(RuntimeError, match="refusing to wipe"):
        _sched.save_jobs([], tmp_paths, _input_count=1)
    # File still has the job.
    assert len(_sched.load_jobs(tmp_paths)) == 1


def test_cron_should_fire_due_in_window():
    # "* * * * *" — fires every minute. last_fired_at=0 → must fire.
    assert _sched.cron_should_fire("* * * * *", "UTC", 0) is True


def test_cron_should_fire_already_fired():
    now = int(time.time())
    # Just fired this minute → must NOT fire again.
    assert _sched.cron_should_fire("* * * * *", "UTC", now, now=now) is False


def test_resolve_target_agent_research_monitor():
    j = _make_job(name="research-monitor:brand-mentions")
    assert _sched.resolve_target_agent(j) == "@research-brand-mentions"


def test_resolve_target_agent_polymarket():
    assert _sched.resolve_target_agent(_make_job(name="polymarket:trading-run")) == "@polymarket"


def test_resolve_target_agent_briefing():
    assert _sched.resolve_target_agent(_make_job(name="Morning briefing")) == "@briefing"


def test_run_due_jobs_updates_last_fired_at(tmp_paths):
    j = _make_job(cron_expr="* * * * *", last_fired_at=0)
    _sched.save_jobs([j], tmp_paths, _input_count=1)

    fixed_now = int(time.time())
    with mock.patch("metasphere.schedule.dispatch_to_agent", return_value=True) as disp:
        results = _sched.run_due_jobs(tmp_paths, now=fixed_now)

    assert len(results) == 1
    assert results[0].fired and results[0].dispatched
    disp.assert_called_once()

    reloaded = _sched.load_jobs(tmp_paths)
    assert reloaded[0].last_fired_at == fixed_now


def test_dispatch_prefers_metasphere_wake_when_mission_exists(tmp_paths):
    target = "@research-brand-mentions"
    agent_dir = tmp_paths.agent_dir(target)
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "MISSION.md").write_text("mission\n")

    wake_path = tmp_paths.repo / "scripts" / "metasphere-wake"
    wake_path.parent.mkdir(parents=True, exist_ok=True)
    wake_path.write_text("#!/bin/sh\nexit 0\n")
    wake_path.chmod(0o755)

    with mock.patch("metasphere.schedule.subprocess.run") as run_mock:
        run_mock.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        ok = _sched.dispatch_to_agent(target, "payload-text", paths=tmp_paths)

    assert ok is True
    run_mock.assert_called_once()
    argv = run_mock.call_args[0][0]
    assert argv[0] == str(wake_path)
    assert argv[1] == target
    assert argv[2] == "payload-text"
