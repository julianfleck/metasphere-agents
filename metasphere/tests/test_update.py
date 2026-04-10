"""Tests for metasphere.update (auto-update config + scheduler integration)."""

from __future__ import annotations

from pathlib import Path

import pytest

from metasphere import schedule as _sched
from metasphere import update as _update
from metasphere.update import AutoUpdateConfig, UpdateResult


# ---------- config parsing ----------

def test_parse_env_text_defaults():
    cfg = _update.parse_env_text("")
    assert cfg.enabled is False
    assert cfg.interval == "daily"
    assert cfg.branch == "main"
    assert cfg.restart_daemons is True
    assert cfg.notify is True


def test_parse_env_text_full():
    text = """
    # comment
    AUTO_UPDATE_ENABLED=true
    AUTO_UPDATE_INTERVAL=hourly
    AUTO_UPDATE_BRANCH="staging"
    AUTO_UPDATE_RESTART_DAEMONS=false
    AUTO_UPDATE_NOTIFY=0
    """
    cfg = _update.parse_env_text(text)
    assert cfg.enabled is True
    assert cfg.interval == "hourly"
    assert cfg.branch == "staging"
    assert cfg.restart_daemons is False
    assert cfg.notify is False


def test_save_load_roundtrip(tmp_paths):
    cfg = AutoUpdateConfig(enabled=True, interval="6h", branch="main")
    p = _update.save_config(cfg, tmp_paths)
    assert p.exists()
    loaded = _update.load_config(tmp_paths)
    assert loaded == cfg


def test_interval_to_cron_keywords():
    assert _update.interval_to_cron("daily") == "0 4 * * *"
    assert _update.interval_to_cron("hourly") == "0 * * * *"
    assert _update.interval_to_cron("6h") == "0 */6 * * *"


def test_interval_to_cron_passthrough_custom():
    assert _update.interval_to_cron("*/15 * * * *") == "*/15 * * * *"


def test_interval_to_cron_unknown_falls_back_daily():
    assert _update.interval_to_cron("garbage") == "0 4 * * *"


# ---------- schedule integration ----------

def test_register_job_creates(tmp_paths):
    cfg = AutoUpdateConfig(enabled=True, interval="hourly")
    job = _update.register_job(cfg, tmp_paths)
    assert job.id == _update.JOB_ID
    assert job.cron_expr == "0 * * * *"
    assert job.enabled is True
    jobs = _sched.load_jobs(tmp_paths)
    assert any(j.id == _update.JOB_ID for j in jobs)


def test_register_job_updates_existing(tmp_paths):
    _update.register_job(AutoUpdateConfig(enabled=True, interval="hourly"), tmp_paths)
    _update.register_job(AutoUpdateConfig(enabled=False, interval="6h"), tmp_paths)
    jobs = _sched.load_jobs(tmp_paths)
    matching = [j for j in jobs if j.id == _update.JOB_ID]
    assert len(matching) == 1
    assert matching[0].cron_expr == "0 */6 * * *"
    assert matching[0].enabled is False


def test_register_job_preserves_last_fired_at(tmp_paths):
    _update.register_job(AutoUpdateConfig(enabled=True), tmp_paths)
    # Mutate last_fired_at then re-register.
    with _sched.with_locked_jobs(tmp_paths) as jobs:
        for j in jobs:
            if j.id == _update.JOB_ID:
                j.last_fired_at = 12345
        _sched.save_jobs(jobs, tmp_paths, _input_count=len(jobs))
    _update.register_job(AutoUpdateConfig(enabled=True, interval="hourly"), tmp_paths)
    jobs = _sched.load_jobs(tmp_paths)
    j = next(j for j in jobs if j.id == _update.JOB_ID)
    assert j.last_fired_at == 12345


def test_unregister_job_removes(tmp_paths):
    _update.register_job(AutoUpdateConfig(enabled=True), tmp_paths)
    assert _update.unregister_job(tmp_paths) is True
    jobs = _sched.load_jobs(tmp_paths)
    assert not any(j.id == _update.JOB_ID for j in jobs)


def test_unregister_job_noop_when_missing(tmp_paths):
    assert _update.unregister_job(tmp_paths) is False


# ---------- update flow (dry, monkeypatched) ----------

def _mk_runner(responses):
    """Build a fake git runner returning canned CompletedProcess-likes."""
    import subprocess

    calls: list[list[str]] = []

    def runner(args):
        calls.append(args)
        key = args[0] if args else ""
        out = responses.get(key, "")
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=out, stderr="")

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


def test_run_update_happy_path(tmp_paths, monkeypatch):
    head_seq = iter(["aaaa1111", "bbbb2222"])
    responses = {
        "rev-parse": "",
        "log": "fix one\nfix two\n",
        "diff": "metasphere/update.py\npyproject.toml\n",
    }

    def fake_runner(args):
        import subprocess
        if args[0] == "rev-parse":
            return subprocess.CompletedProcess(args, 0, next(head_seq), "")
        return subprocess.CompletedProcess(args, 0, responses.get(args[0], ""), "")

    sent = []
    pip_calls: list[list[str]] = []
    bash_calls: list[Path] = []

    def fake_bash(repo):
        bash_calls.append(repo)
        return 0

    def fake_pip(args):
        pip_calls.append(args)
        return 0

    cfg = AutoUpdateConfig(enabled=True, notify=True)
    result = _update.run_update(
        paths=tmp_paths,
        cfg=cfg,
        quiet=True,
        git_runner=fake_runner,
        pip_runner=fake_pip,
        test_runner=lambda: True,
        bash_update=fake_bash,
        notify_sender=lambda msg: sent.append(msg),
    )
    assert result.ok is True
    assert result.old_hash == "aaaa1111"
    assert result.new_hash == "bbbb2222"
    assert result.commits == 2
    assert result.pip_reinstalled is True
    assert result.tests_passed is True
    assert result.daemons_restarted is True
    assert bash_calls == [tmp_paths.project_root]
    assert pip_calls and pip_calls[0][:3] == ["-m", "pip", "install"]
    assert sent and "auto-update" in sent[0]
    assert "bbbb2222"[:10] in sent[0]
    # State persisted
    state = _update.load_state(tmp_paths)
    assert state["last_result"]["ok"] is True


def test_run_update_bash_failure_skips_restart_and_notifies(tmp_paths):
    sent = []

    def fake_bash(_repo):
        return 2

    def fake_runner(args):
        import subprocess
        return subprocess.CompletedProcess(args, 0, "deadbeef", "")

    result = _update.run_update(
        paths=tmp_paths,
        cfg=AutoUpdateConfig(enabled=True, notify=True),
        quiet=True,
        git_runner=fake_runner,
        bash_update=fake_bash,
        notify_sender=lambda msg: sent.append(msg),
    )
    assert result.ok is False
    assert "bash update" in result.reason
    assert sent and "FAILED" in sent[0]


def test_run_update_test_gate_failure(tmp_paths):
    head_seq = iter(["aaaa", "bbbb"])

    def fake_runner(args):
        import subprocess
        if args[0] == "rev-parse":
            return subprocess.CompletedProcess(args, 0, next(head_seq), "")
        if args[0] == "log":
            return subprocess.CompletedProcess(args, 0, "x\n", "")
        if args[0] == "diff":
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    result = _update.run_update(
        paths=tmp_paths,
        cfg=AutoUpdateConfig(enabled=True, notify=False),
        quiet=True,
        git_runner=fake_runner,
        bash_update=lambda r: 0,
        test_runner=lambda: False,
    )
    assert result.ok is False
    assert result.reason == "test gate failed"
    assert result.tests_passed is False


def test_run_update_no_python_changes_skips_pip(tmp_paths):
    head_seq = iter(["aaaa", "bbbb"])
    pip_calls: list[list[str]] = []

    def fake_runner(args):
        import subprocess
        if args[0] == "rev-parse":
            return subprocess.CompletedProcess(args, 0, next(head_seq), "")
        if args[0] == "log":
            return subprocess.CompletedProcess(args, 0, "doc fix\n", "")
        if args[0] == "diff":
            return subprocess.CompletedProcess(args, 0, "docs/foo.md\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    result = _update.run_update(
        paths=tmp_paths,
        cfg=AutoUpdateConfig(enabled=True, notify=False),
        quiet=True,
        git_runner=fake_runner,
        bash_update=lambda r: 0,
        pip_runner=lambda args: pip_calls.append(args) or 0,
        test_runner=lambda: True,
    )
    assert result.ok is True
    assert result.pip_reinstalled is False
    assert pip_calls == []


# ---------- status ----------

def test_status_text_unconfigured(tmp_paths):
    out = _update.status_text(tmp_paths)
    assert "enabled:" in out
    assert "False" in out
    assert "(not registered)" in out


def test_status_text_after_enable(tmp_paths):
    _update.save_config(AutoUpdateConfig(enabled=True, interval="hourly"), tmp_paths)
    _update.register_job(AutoUpdateConfig(enabled=True, interval="hourly"), tmp_paths)
    out = _update.status_text(tmp_paths)
    assert "True" in out
    assert "0 * * * *" in out
    assert "(not registered)" not in out


# ---------- CLI dispatcher ----------

def test_cli_enable_disable_status(tmp_paths, capsys):
    from metasphere.cli import update as cli_update

    rc = cli_update.main(["--enable"])
    assert rc == 0
    cfg = _update.load_config(tmp_paths)
    assert cfg.enabled is True
    jobs = _sched.load_jobs(tmp_paths)
    assert any(j.id == _update.JOB_ID for j in jobs)

    rc = cli_update.main(["--status"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "enabled:" in out

    rc = cli_update.main(["--disable"])
    assert rc == 0
    assert _update.load_config(tmp_paths).enabled is False
    jobs = _sched.load_jobs(tmp_paths)
    assert not any(j.id == _update.JOB_ID for j in jobs)


def test_cli_register_job(tmp_paths):
    from metasphere.cli import update as cli_update
    _update.save_config(AutoUpdateConfig(enabled=True, interval="6h"), tmp_paths)
    rc = cli_update.main(["--register-job"])
    assert rc == 0
    jobs = _sched.load_jobs(tmp_paths)
    j = next(j for j in jobs if j.id == _update.JOB_ID)
    assert j.cron_expr == "0 */6 * * *"
