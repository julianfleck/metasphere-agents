import subprocess
from pathlib import Path

import pytest

from metasphere.git_hooks import (
    HOOKS,
    handle_post_commit,
    handle_pre_commit,
    hooks_status,
    install_hooks,
    uninstall_hooks,
)


def _init_repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    return repo


def test_install_creates_executable_shims(tmp_path):
    repo = _init_repo(tmp_path)
    written = install_hooks(repo)
    assert sorted(written) == sorted(HOOKS)
    for hook in HOOKS:
        f = repo / ".git" / "hooks" / hook
        assert f.exists()
        assert f.stat().st_mode & 0o100  # user-executable
        body = f.read_text()
        assert "Metasphere managed hook" in body
        assert f"metasphere.cli.git_hooks {hook}" in body
        # gap-5: shim must resolve interpreter at run time, not bake an
        # absolute install-time path.
        assert "command -v python3" in body
        assert "python3" in body


def test_install_backs_up_existing(tmp_path):
    repo = _init_repo(tmp_path)
    pre = repo / ".git" / "hooks" / "pre-commit"
    pre.write_text("#!/bin/sh\necho legacy\n")
    install_hooks(repo)
    assert (repo / ".git" / "hooks" / "pre-commit.backup").exists()


def test_uninstall_restores_backup(tmp_path):
    repo = _init_repo(tmp_path)
    pre = repo / ".git" / "hooks" / "pre-commit"
    pre.write_text("#!/bin/sh\necho legacy\n")
    install_hooks(repo)
    removed = uninstall_hooks(repo)
    assert "pre-commit" in removed
    assert pre.exists()
    assert "legacy" in pre.read_text()


def test_status_reports_states(tmp_path):
    repo = _init_repo(tmp_path)
    install_hooks(repo)
    st = hooks_status(repo)
    assert all(v == "metasphere" for v in st.values())
    uninstall_hooks(repo)
    st = hooks_status(repo)
    assert all(v == "missing" for v in st.values())


def test_install_rejects_non_repo(tmp_path):
    with pytest.raises(FileNotFoundError):
        install_hooks(tmp_path / "nope")


def test_handle_pre_commit_logs_event(tmp_paths):
    rc = handle_pre_commit(paths=tmp_paths)
    assert rc == 0
    log = tmp_paths.events_log
    assert log.exists()
    assert "git.pre-commit" in log.read_text()


def test_handle_post_commit_logs_event(tmp_paths, tmp_path, monkeypatch):
    repo = tmp_path / "subrepo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t",
                    "commit", "--allow-empty", "-m", "hello", "-q"],
                   cwd=repo, check=True)
    rc = handle_post_commit(paths=tmp_paths)
    assert rc == 0
    text = tmp_paths.events_log.read_text()
    assert "git.commit" in text
    assert "hello" in text
