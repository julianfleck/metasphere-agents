"""Tests for metasphere.schedule."""

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


def test_dispatch_prefers_wake_persistent_when_global_mission_exists(tmp_paths):
    target = "@polymarket"
    agent_dir = tmp_paths.agent_dir(target)
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "MISSION.md").write_text("mission\n")

    with mock.patch("metasphere.schedule._agents.wake_persistent") as wake_mock:
        wake_mock.return_value = mock.Mock()
        ok = _sched.dispatch_to_agent(target, "payload-text", paths=tmp_paths)

    assert ok is True
    wake_mock.assert_called_once()
    # wake_persistent(target, first_task=payload, paths=tmp_paths)
    args, kwargs = wake_mock.call_args
    assert args[0] == target
    assert kwargs.get("first_task") == "payload-text"
    assert kwargs.get("paths") is tmp_paths


def test_dispatch_wakes_project_scoped_persistent_agent(tmp_paths):
    """Project-scoped research agents live under
    ``projects/<proj>/agents/@name/MISSION.md``. Dispatching must find
    them too, or @research-* jobs pile up unread (Julian's bug report)."""
    target = "@research-brand-mentions"
    proj_agent_dir = tmp_paths.project_agent_dir("research", target)
    proj_agent_dir.mkdir(parents=True, exist_ok=True)
    (proj_agent_dir / "MISSION.md").write_text("mission\n")

    with mock.patch("metasphere.schedule._agents.wake_persistent") as wake_mock:
        wake_mock.return_value = mock.Mock()
        ok = _sched.dispatch_to_agent(target, "scan now", paths=tmp_paths)

    assert ok is True
    wake_mock.assert_called_once()
    args, kwargs = wake_mock.call_args
    assert args[0] == target
    assert kwargs.get("first_task") == "scan now"


def test_dispatch_to_agent_falls_back_to_inbox_when_wake_fails(tmp_paths):
    target = "@polymarket"
    agent_dir = tmp_paths.agent_dir(target)
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "MISSION.md").write_text("mission\n")

    with mock.patch(
        "metasphere.schedule._agents.wake_persistent",
        side_effect=RuntimeError("tmux died"),
    ), mock.patch("metasphere.schedule.send_message") as send_mock:
        send_mock.return_value = mock.Mock()
        ok = _sched.dispatch_to_agent(target, "payload", paths=tmp_paths)

    assert ok is True
    send_mock.assert_called_once()


def test_dispatch_to_agent_ephemeral_uses_inbox(tmp_paths):
    # No MISSION.md anywhere → drop to inbox, no wake.
    target = "@someone"
    with mock.patch("metasphere.schedule._agents.wake_persistent") as wake_mock, \
            mock.patch("metasphere.schedule.send_message") as send_mock:
        send_mock.return_value = mock.Mock()
        ok = _sched.dispatch_to_agent(target, "payload", paths=tmp_paths)

    assert ok is True
    wake_mock.assert_not_called()
    send_mock.assert_called_once()


# ---------- dispatch_command: wake-before-send ----------


def test_extract_messages_send_target_bare_command():
    assert (
        _sched._extract_messages_send_target(
            'messages send @polymarket !task "run pipeline"'
        )
        == "@polymarket"
    )


def test_extract_messages_send_target_full_path():
    assert (
        _sched._extract_messages_send_target(
            '/usr/local/bin/messages send @research-brand !task "scan"'
        )
        == "@research-brand"
    )


def test_extract_messages_send_target_not_a_send_command():
    assert _sched._extract_messages_send_target("echo hi") is None
    assert _sched._extract_messages_send_target("messages inbox") is None
    # Send but no @-target (shouldn't happen, but don't crash).
    assert _sched._extract_messages_send_target("messages send !task hi") is None


def test_extract_messages_send_target_malformed_payload():
    # Unbalanced quote → shlex raises → return None cleanly.
    assert _sched._extract_messages_send_target('messages send @x "oops') is None


def test_dispatch_command_pre_wakes_messages_send_task_target(tmp_paths):
    """The main regression fix: scheduled `messages send @polymarket !task`
    commands must cold-start the agent's tmux+REPL before sending, so
    the inbox notice has a live session to inject into."""
    target = "@polymarket"
    agent_dir = tmp_paths.agent_dir(target)
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "MISSION.md").write_text("mission\n")

    with mock.patch("metasphere.schedule._agents.wake_persistent") as wake_mock, \
            mock.patch("metasphere.schedule.subprocess.run") as run_mock:
        wake_mock.return_value = mock.Mock()
        run_mock.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        ok = _sched.dispatch_command(
            'messages send @polymarket !task "run the poly pipeline"',
            paths=tmp_paths,
        )

    assert ok is True
    wake_mock.assert_called_once()
    args, kwargs = wake_mock.call_args
    assert args[0] == target
    # Pre-wake should not pass a first_task — the subsequent shell
    # command carries the actual inbox notice.
    assert kwargs.get("first_task") is None
    # The real shell command must still run after the pre-wake.
    run_mock.assert_called_once()


def test_dispatch_command_pre_wakes_project_scoped_research_target(tmp_paths):
    target = "@research-brand-mentions"
    proj_agent_dir = tmp_paths.project_agent_dir("research", target)
    proj_agent_dir.mkdir(parents=True, exist_ok=True)
    (proj_agent_dir / "MISSION.md").write_text("mission\n")

    with mock.patch("metasphere.schedule._agents.wake_persistent") as wake_mock, \
            mock.patch("metasphere.schedule.subprocess.run") as run_mock:
        wake_mock.return_value = mock.Mock()
        run_mock.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        ok = _sched.dispatch_command(
            'messages send @research-brand-mentions !task "do the scan"',
            paths=tmp_paths,
        )

    assert ok is True
    wake_mock.assert_called_once()


def test_dispatch_command_skips_wake_for_ephemeral_target(tmp_paths):
    # No MISSION.md — nothing to wake, command still runs.
    with mock.patch("metasphere.schedule._agents.wake_persistent") as wake_mock, \
            mock.patch("metasphere.schedule.subprocess.run") as run_mock:
        run_mock.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        ok = _sched.dispatch_command(
            'messages send @ephemeral !task "x"',
            paths=tmp_paths,
        )

    assert ok is True
    wake_mock.assert_not_called()
    run_mock.assert_called_once()


def test_dispatch_command_does_not_wake_for_non_send_command(tmp_paths):
    # Arbitrary command, not `messages send` — never wake.
    target_dir = tmp_paths.agent_dir("@polymarket")
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / "MISSION.md").write_text("mission\n")

    with mock.patch("metasphere.schedule._agents.wake_persistent") as wake_mock, \
            mock.patch("metasphere.schedule.subprocess.run") as run_mock:
        run_mock.return_value = mock.Mock(returncode=0, stdout="", stderr="")
        ok = _sched.dispatch_command("echo hello", paths=tmp_paths)

    assert ok is True
    wake_mock.assert_not_called()
