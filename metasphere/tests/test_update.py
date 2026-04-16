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


def _patch_update_helpers(monkeypatch, *, pull_raises=None, sync_calls=None,
                          restart_calls=None):
    """Monkeypatch the three module-level side-effect helpers.

    Returns nothing; call sites read the list parameters they passed in.
    """
    def fake_pull(repo, branch, runner):
        if pull_raises is not None:
            raise pull_raises
        return None

    def fake_sync(repo, home_dir):
        if sync_calls is not None:
            sync_calls.append((repo, home_dir))

    def fake_restart():
        if restart_calls is not None:
            restart_calls.append(True)

    monkeypatch.setattr(_update, "_git_pull_or_reset", fake_pull)
    monkeypatch.setattr(_update, "_sync_claude_integration", fake_sync)
    monkeypatch.setattr(_update, "_restart_daemons", fake_restart)


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
    sync_calls: list = []
    restart_calls: list = []

    _patch_update_helpers(
        monkeypatch, sync_calls=sync_calls, restart_calls=restart_calls,
    )

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
        notify_sender=lambda msg: sent.append(msg),
    )
    assert result.ok is True
    assert result.old_hash == "aaaa1111"
    assert result.new_hash == "bbbb2222"
    assert result.commits == 2
    assert result.pip_reinstalled is True
    assert result.tests_passed is True
    assert result.daemons_restarted is True
    assert sync_calls and sync_calls[0][0] == tmp_paths.project_root
    assert restart_calls == [True]
    assert pip_calls and pip_calls[0][:3] == ["-m", "pip", "install"]
    assert sent and "auto-update" in sent[0]
    assert "bbbb2222"[:10] in sent[0]
    # State persisted
    state = _update.load_state(tmp_paths)
    assert state["last_result"]["ok"] is True


def test_run_update_git_pull_failure_skips_restart_and_notifies(tmp_paths, monkeypatch):
    sent = []
    restart_calls: list = []

    _patch_update_helpers(
        monkeypatch,
        pull_raises=RuntimeError("git fetch origin failed (rc=1)"),
        restart_calls=restart_calls,
    )

    def fake_runner(args):
        import subprocess
        return subprocess.CompletedProcess(args, 0, "deadbeef", "")

    result = _update.run_update(
        paths=tmp_paths,
        cfg=AutoUpdateConfig(enabled=True, notify=True),
        quiet=True,
        git_runner=fake_runner,
        notify_sender=lambda msg: sent.append(msg),
    )
    assert result.ok is False
    assert "git pull failed" in result.reason
    # Daemon restart must NOT have run on a failed pull.
    assert restart_calls == []
    assert sent and "FAILED" in sent[0]


def test_run_update_restart_skipped_when_cfg_disables(tmp_paths, monkeypatch):
    head_seq = iter(["aaaa", "bbbb"])
    restart_calls: list = []

    _patch_update_helpers(monkeypatch, restart_calls=restart_calls)

    def fake_runner(args):
        import subprocess
        if args[0] == "rev-parse":
            return subprocess.CompletedProcess(args, 0, next(head_seq), "")
        return subprocess.CompletedProcess(args, 0, "", "")

    result = _update.run_update(
        paths=tmp_paths,
        cfg=AutoUpdateConfig(enabled=True, notify=False, restart_daemons=False),
        quiet=True,
        git_runner=fake_runner,
        test_runner=lambda: True,
    )
    assert result.ok is True
    assert result.daemons_restarted is False
    assert restart_calls == []


def test_run_update_test_gate_failure(tmp_paths, monkeypatch):
    head_seq = iter(["aaaa", "bbbb"])
    _patch_update_helpers(monkeypatch)

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
        test_runner=lambda: False,
    )
    assert result.ok is False
    assert result.reason == "test gate failed"
    assert result.tests_passed is False


def test_run_update_no_python_changes_skips_pip(tmp_paths, monkeypatch):
    head_seq = iter(["aaaa", "bbbb"])
    pip_calls: list[list[str]] = []
    _patch_update_helpers(monkeypatch)

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
        pip_runner=lambda args: pip_calls.append(args) or 0,
        test_runner=lambda: True,
    )
    assert result.ok is True
    assert result.pip_reinstalled is False
    assert pip_calls == []


# ---------- helper unit tests ----------

def test_git_pull_or_reset_happy_path():
    """Clean tree → status porcelain empty → pull --ff-only succeeds."""
    import subprocess as _sp
    calls: list[list[str]] = []

    def runner(args):
        calls.append(args)
        if args[0] == "status":
            return _sp.CompletedProcess(args, 0, "", "")  # clean
        return _sp.CompletedProcess(args, 0, "", "")

    _update._git_pull_or_reset(Path("/tmp"), "main", runner)
    # status check happens first, then pull.
    assert calls[0] == ["status", "--porcelain"]
    assert ["pull", "--ff-only", "origin", "main"] in calls


def test_git_pull_or_reset_fallback_to_reset():
    """Clean tree, pull fails → fetch + reset --hard fallback runs."""
    import subprocess as _sp

    def runner(args):
        if args[0] == "status":
            return _sp.CompletedProcess(args, 0, "", "")  # clean
        if args[0] == "pull":
            return _sp.CompletedProcess(args, 1, "", "conflict")
        return _sp.CompletedProcess(args, 0, "", "")

    _update._git_pull_or_reset(Path("/tmp"), "main", runner)


def test_git_pull_or_reset_raises_when_reset_fails():
    import subprocess as _sp

    def runner(args):
        if args[0] == "status":
            return _sp.CompletedProcess(args, 0, "", "")  # clean
        if args[0] == "pull":
            return _sp.CompletedProcess(args, 1, "", "")
        if args[0] == "fetch":
            return _sp.CompletedProcess(args, 1, "", "network down")
        return _sp.CompletedProcess(args, 0, "", "")

    with pytest.raises(RuntimeError, match="git fetch"):
        _update._git_pull_or_reset(Path("/tmp"), "main", runner)


def test_git_pull_or_reset_refuses_on_dirty_tree():
    """Dirty tree → refuse with a RuntimeError listing the paths.

    Regression: 2026-04-16 an auto-triggered `metasphere update` ran
    against a working tree with 10 files of uncommitted WIP, hit a
    pull conflict, fell through to `git reset --hard origin/main`, and
    silently destroyed the WIP (irrecoverable from reflog). The fix
    refuses to proceed when the tree is dirty so the human has to
    commit/stash/discard explicitly.
    """
    import subprocess as _sp
    calls: list[list[str]] = []

    def runner(args):
        calls.append(args)
        if args[0] == "status":
            return _sp.CompletedProcess(
                args, 0,
                " M metasphere/tmux.py\n M metasphere/heartbeat.py\n?? new_file.py\n",
                "",
            )
        return _sp.CompletedProcess(args, 0, "", "")

    with pytest.raises(RuntimeError, match="uncommitted changes"):
        _update._git_pull_or_reset(Path("/tmp"), "main", runner)

    # Critical: pull/fetch/reset must NOT have been called.
    for c in calls:
        assert c[0] not in ("pull", "fetch", "reset"), (
            f"dirty-tree refusal must abort BEFORE touching remote, got {c}"
        )


def test_git_pull_or_reset_refuses_when_status_fails():
    """Unusable `git status` → fail closed (treat as dirty)."""
    import subprocess as _sp

    def runner(args):
        if args[0] == "status":
            return _sp.CompletedProcess(args, 128, "", "not a git repo")
        return _sp.CompletedProcess(args, 0, "", "")

    with pytest.raises(RuntimeError, match="uncommitted|git status"):
        _update._git_pull_or_reset(Path("/tmp"), "main", runner)


def test_sync_claude_integration_creates_symlinks(tmp_path):
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    # Build a fake repo with one skill and one command.
    skill_dir = repo / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# demo\n")
    cmd_dir = repo / ".claude" / "commands"
    cmd_dir.mkdir(parents=True)
    (cmd_dir / "foo.md").write_text("cmd\n")

    _update._sync_claude_integration(repo, home)

    skill_link = home / ".claude" / "skills" / "demo"
    cmd_link = home / ".claude" / "commands" / "foo.md"
    assert skill_link.is_symlink()
    assert skill_link.resolve() == skill_dir.resolve()
    assert cmd_link.is_symlink()
    assert cmd_link.resolve() == (cmd_dir / "foo.md").resolve()


def test_sync_claude_integration_respects_user_customized(tmp_path):
    repo = tmp_path / "repo"
    home = tmp_path / "home"
    skill_dir = repo / "skills" / "demo"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# demo\n")

    # Pre-create a real dir with .user-customized marker.
    user_dir = home / ".claude" / "skills" / "demo"
    user_dir.mkdir(parents=True)
    (user_dir / ".user-customized").write_text("keep\n")
    (user_dir / "SKILL.md").write_text("# customized\n")

    _update._sync_claude_integration(repo, home)

    assert user_dir.is_dir() and not user_dir.is_symlink()
    assert (user_dir / ".user-customized").is_file()


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
