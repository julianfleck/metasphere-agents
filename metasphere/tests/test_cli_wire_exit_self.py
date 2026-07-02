"""Tests for metasphere.cli.wire_exit_self."""

from __future__ import annotations

import pytest

from metasphere import schedule as _sched
from metasphere.cli import wire_exit_self as _wes
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


# ---------- _appended_payload ----------


def test_appended_payload_empty_existing():
    out = _wes._appended_payload("")
    assert out == _wes.CLEANUP_STANZA


def test_appended_payload_none_existing():
    out = _wes._appended_payload(None)  # type: ignore[arg-type]
    assert out == _wes.CLEANUP_STANZA


def test_appended_payload_existing_no_trailing_newline():
    out = _wes._appended_payload("body text")
    assert out == "body text\n\n" + _wes.CLEANUP_STANZA


def test_appended_payload_strips_trailing_whitespace():
    out = _wes._appended_payload("body text\n\n  \n")
    assert out == "body text\n\n" + _wes.CLEANUP_STANZA


# ---------- wire_exit_self ----------


def test_wire_exit_self_modifies_flagged_job(tmp_paths):
    j = _make_job(wants_exit_self_cleanup=True, payload_message="hello")
    _sched.save_jobs([j], tmp_paths, _input_count=1)

    result = _wes.wire_exit_self(paths=tmp_paths)

    assert result["modified"] == [j.name]
    assert result["skipped"] == []
    reloaded = _sched.load_jobs(tmp_paths)
    assert _wes.SENTINEL in reloaded[0].payload_message


def test_wire_exit_self_skips_unflagged_job(tmp_paths):
    j = _make_job(wants_exit_self_cleanup=False, payload_message="hello")
    _sched.save_jobs([j], tmp_paths, _input_count=1)

    result = _wes.wire_exit_self(paths=tmp_paths)

    assert result == {"modified": [], "skipped": []}
    reloaded = _sched.load_jobs(tmp_paths)
    assert reloaded[0].payload_message == "hello"


def test_wire_exit_self_idempotent_on_sentinel(tmp_paths):
    pre_wired = "hello\n\nrun metasphere session exit-self after"
    j = _make_job(wants_exit_self_cleanup=True, payload_message=pre_wired)
    _sched.save_jobs([j], tmp_paths, _input_count=1)

    result = _wes.wire_exit_self(paths=tmp_paths)

    assert result == {"modified": [], "skipped": [j.name]}
    reloaded = _sched.load_jobs(tmp_paths)
    assert reloaded[0].payload_message == pre_wired


def test_wire_exit_self_dry_run_does_not_persist(tmp_paths):
    j = _make_job(wants_exit_self_cleanup=True, payload_message="hello")
    _sched.save_jobs([j], tmp_paths, _input_count=1)

    result = _wes.wire_exit_self(paths=tmp_paths, dry_run=True)

    assert result["modified"] == [j.name]
    reloaded = _sched.load_jobs(tmp_paths)
    assert reloaded[0].payload_message == "hello"
    assert _wes.SENTINEL not in reloaded[0].payload_message


def test_wire_exit_self_mixed_flagged_and_skipped(tmp_paths):
    a = _make_job(
        id="a", source_id="a", name="a", wants_exit_self_cleanup=True,
        payload_message="needs wiring",
    )
    b = _make_job(
        id="b", source_id="b", name="b", wants_exit_self_cleanup=False,
        payload_message="leave alone",
    )
    c = _make_job(
        id="c", source_id="c", name="c", wants_exit_self_cleanup=True,
        payload_message=f"already has {_wes.SENTINEL} stanza",
    )
    _sched.save_jobs([a, b, c], tmp_paths, _input_count=3)

    result = _wes.wire_exit_self(paths=tmp_paths)

    assert result["modified"] == ["a"]
    assert result["skipped"] == ["c"]
    reloaded = {j.name: j for j in _sched.load_jobs(tmp_paths)}
    assert _wes.SENTINEL in reloaded["a"].payload_message
    assert reloaded["b"].payload_message == "leave alone"
    assert reloaded["c"].payload_message == f"already has {_wes.SENTINEL} stanza"


# ---------- main (CLI) ----------


def test_main_help_returns_zero(capsys):
    rc = _wes.main(["--help"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "wire-exit-self" in out


def test_main_unknown_arg_returns_two(capsys):
    rc = _wes.main(["--bogus"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "unknown args" in err


def test_main_dry_run_reports_modify_count(tmp_paths, capsys):
    j = _make_job(wants_exit_self_cleanup=True, payload_message="hello")
    _sched.save_jobs([j], tmp_paths, _input_count=1)

    rc = _wes.main(["--dry-run"])

    assert rc == 0
    out = capsys.readouterr().out
    assert "would modify: 1 job(s)" in out
    assert f"+ {j.name}" in out
    # Dry-run must not persist.
    reloaded = _sched.load_jobs(tmp_paths)
    assert _wes.SENTINEL not in reloaded[0].payload_message


def test_main_real_run_persists_and_reports(tmp_paths, capsys):
    j = _make_job(wants_exit_self_cleanup=True, payload_message="hello")
    _sched.save_jobs([j], tmp_paths, _input_count=1)

    rc = _wes.main([])

    assert rc == 0
    out = capsys.readouterr().out
    assert "modified: 1 job(s)" in out
    reloaded = _sched.load_jobs(tmp_paths)
    assert _wes.SENTINEL in reloaded[0].payload_message
