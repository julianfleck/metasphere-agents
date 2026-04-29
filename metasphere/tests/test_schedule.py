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
    # research-monitor:X resolves to @X, NOT @research-X. The persistent
    # agents under projects/research/agents/ are named @brand-mentions,
    # @divergence-engines, etc. — without the "research-" prefix, because
    # the enclosing project directory is already named "research".
    #
    # This has regressed twice (39f22fc fixed → 0808693 reverted).
    # If this assertion looks wrong to you, check the filesystem before
    # "fixing" the production code — `ls ~/.metasphere/projects/research/agents/`
    # is the ground truth.
    j = _make_job(name="research-monitor:brand-mentions")
    assert _sched.resolve_target_agent(j) == "@brand-mentions"


def test_resolve_target_agent_research_monitor_multiple_areas():
    # All research-monitor:X schedules share the same resolution rule.
    # Asserting multiple forms makes it harder to re-regress by
    # tweaking the test for a single case.
    for area in [
        "brand-mentions",
        "divergence-engines",
        "memory-architectures",
        "residency-programs",
        "job-opportunities",
        "evaluation-governance",
        "retrieval-architectures",
        "accelerator-programs",
        "agentic-reasoning",
        "ephemeral-interfaces",
    ]:
        j = _make_job(name=f"research-monitor:{area}")
        assert _sched.resolve_target_agent(j) == f"@{area}", (
            f"research-monitor:{area} should map to @{area}, not @research-{area} "
            f"(agents live at projects/research/agents/@{area}/)"
        )


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


def test_run_due_jobs_persists_last_fired_before_dispatch(tmp_paths):
    """If a dispatch crashes the daemon mid-fire (e.g. metasphere update
    restarting metasphere-schedule), last_fired_at must already be on
    disk so the next tick doesn't re-fire within the cron window. This
    is the 04:01-04:03Z 2026-04-27 auto-update storm scenario."""
    j = _make_job(cron_expr="* * * * *", last_fired_at=0, payload_kind="command")
    _sched.save_jobs([j], tmp_paths, _input_count=1)

    # Pin to second 5 of the current minute so the second call at
    # ``fixed_now + 15`` stays within the same minute. Without this,
    # CI runs that landed at second >=45 of a minute saw the second
    # call cross the next ``* * * * *`` boundary and re-fire legit.
    fixed_now = (int(time.time()) // 60) * 60 + 5
    last_fired_during_dispatch: list[int] = []

    def _crashing_dispatch(*_args, **_kwargs):
        # Simulate the daemon being able to read jobs.json mid-dispatch
        # (i.e. another process). Stamp must already be persisted.
        reloaded = _sched.load_jobs(tmp_paths)
        last_fired_during_dispatch.append(reloaded[0].last_fired_at)
        raise RuntimeError("simulated daemon restart mid-dispatch")

    with mock.patch("metasphere.schedule.dispatch_command", side_effect=_crashing_dispatch):
        with pytest.raises(RuntimeError, match="simulated daemon restart"):
            _sched.run_due_jobs(tmp_paths, now=fixed_now)

    assert last_fired_during_dispatch == [fixed_now], (
        "last_fired_at must be persisted BEFORE dispatch runs"
    )

    # Re-running run_due_jobs in the same cron window must NOT re-fire,
    # because last_fired_at == prev_epoch (already-fired guard).
    with mock.patch("metasphere.schedule.dispatch_command") as disp2:
        results2 = _sched.run_due_jobs(tmp_paths, now=fixed_now + 15)
    assert results2 == []
    disp2.assert_not_called()


def test_set_enabled_accepts_id_or_name(tmp_paths):
    j = _make_job(id="metasphere-auto-update", name="metasphere:auto-update", enabled=True)
    _sched.save_jobs([j], tmp_paths, _input_count=1)

    assert _sched.set_enabled("metasphere-auto-update", False, tmp_paths) is True
    assert _sched.load_jobs(tmp_paths)[0].enabled is False

    # Re-enable using the displayed ``name`` (the inconsistency that bit
    # users on 2026-04-27).
    assert _sched.set_enabled("metasphere:auto-update", True, tmp_paths) is True
    assert _sched.load_jobs(tmp_paths)[0].enabled is True

    assert _sched.set_enabled("does-not-exist", False, tmp_paths) is False


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


# ---------- wire-exit-self migration tool ----------

from metasphere.cli.wire_exit_self import (
    SENTINEL,
    TARGET_JOB_NAMES,
    wire_exit_self,
)


def _seed_jobs_for_wire_test(tmp_paths, *, payloads=None):
    """Seed jobs.json with one job per TARGET_JOB_NAMES + one off-target.

    ``payloads`` is an optional dict ``{name: payload_message}``; missing
    entries default to a short instructional body so the append has
    something to attach to.
    """
    payloads = payloads or {}
    jobs = []
    for i, name in enumerate(TARGET_JOB_NAMES):
        body = payloads.get(name, f"Run {name}\nStep 1: do thing\nStep 2: report")
        jobs.append(
            _make_job(
                id=f"job-target-{i}",
                source_id=f"target-{i}",
                name=name,
                payload_message=body,
            )
        )
    # Untouched control: bare-name persistent agent that should not be
    # rewritten even though it's also a cron-fired agentTurn.
    jobs.append(
        _make_job(
            id="job-control-polymarket",
            source_id="control-polymarket",
            name="polymarket:trading-run",
            payload_message="run polymarket pipeline",
        )
    )
    _sched.save_jobs(jobs, tmp_paths, _input_count=len(jobs))


def test_wire_exit_self_appends_to_named_jobs(tmp_paths):
    _seed_jobs_for_wire_test(tmp_paths)
    result = wire_exit_self(tmp_paths)

    assert sorted(result["modified"]) == sorted(TARGET_JOB_NAMES)
    assert result["skipped"] == []
    assert result["not_found"] == []

    saved = {j.name: j for j in _sched.load_jobs(tmp_paths)}
    for name in TARGET_JOB_NAMES:
        assert SENTINEL in saved[name].payload_message, name
        # Original instructions still present at the head of the body.
        assert saved[name].payload_message.startswith(f"Run {name}")

    # Control job — payload must be unchanged.
    assert saved["polymarket:trading-run"].payload_message == "run polymarket pipeline"
    assert SENTINEL not in saved["polymarket:trading-run"].payload_message


def test_wire_exit_self_is_idempotent(tmp_paths):
    _seed_jobs_for_wire_test(tmp_paths)
    first = wire_exit_self(tmp_paths)
    assert len(first["modified"]) == len(TARGET_JOB_NAMES)

    # Capture payload state after first run for byte-for-byte equality.
    after_first = {j.name: j.payload_message for j in _sched.load_jobs(tmp_paths)}

    second = wire_exit_self(tmp_paths)
    assert second["modified"] == []
    assert sorted(second["skipped"]) == sorted(TARGET_JOB_NAMES)

    after_second = {j.name: j.payload_message for j in _sched.load_jobs(tmp_paths)}
    assert after_second == after_first


def test_wire_exit_self_dry_run_does_not_mutate(tmp_paths):
    _seed_jobs_for_wire_test(tmp_paths)
    before = {j.name: j.payload_message for j in _sched.load_jobs(tmp_paths)}

    result = wire_exit_self(tmp_paths, dry_run=True)
    assert sorted(result["modified"]) == sorted(TARGET_JOB_NAMES)

    after = {j.name: j.payload_message for j in _sched.load_jobs(tmp_paths)}
    assert before == after, "dry-run must not write to jobs.json"


def test_wire_exit_self_reports_not_found_targets(tmp_paths):
    # Empty jobs.json — every target reports as not_found.
    _sched.save_jobs(
        [_make_job(id="placeholder", name="other:job")],
        tmp_paths,
        _input_count=1,
    )
    result = wire_exit_self(tmp_paths)
    assert result["modified"] == []
    assert result["skipped"] == []
    assert sorted(result["not_found"]) == sorted(TARGET_JOB_NAMES)


def test_wire_exit_self_preserves_existing_funding_sink_stanza(tmp_paths):
    # 3 of the live research-monitor payloads end with a `---` Funding
    # pipeline sink stanza (added 2026-04-14). The cleanup append must
    # sit BELOW the sink without disturbing it.
    sink_payload = (
        "Run research-monitor for area: residency-programs\n"
        "Steps...\n\n"
        "---\n"
        "**Funding pipeline sink:** copy report to "
        "~/.metasphere/agents/@funding-research/sources/residency/<date>.md"
    )
    job = _make_job(
        id="job-residency",
        name="research-monitor:residency-programs",
        payload_message=sink_payload,
    )
    _sched.save_jobs([job], tmp_paths, _input_count=1)

    result = wire_exit_self(tmp_paths)
    assert result["modified"] == ["research-monitor:residency-programs"]

    saved = _sched.load_jobs(tmp_paths)[0].payload_message
    # Sink stanza is still intact at its original position.
    assert "**Funding pipeline sink:**" in saved
    sink_idx = saved.index("**Funding pipeline sink:**")
    cleanup_idx = saved.index("Session cleanup")
    # Cleanup is appended AFTER the sink — both head and tail divider
    # are preserved.
    assert sink_idx < cleanup_idx
    assert SENTINEL in saved
