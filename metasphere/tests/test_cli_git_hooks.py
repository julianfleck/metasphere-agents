"""CLI-layer tests for ``metasphere hooks git``.

Library-level coverage lives in ``test_git_hooks.py``; this file
exercises the argv-parsing surface — specifically the flag-shaped
path-positional rejection. Without these guards,
``hooks git install --help`` fell through to ``Path('--help')`` and
raised the confusing ``not a git repository: --help`` instead of
printing usage. Same class of bug as df6812e (project init).
"""

from __future__ import annotations

from unittest.mock import patch

from metasphere.cli import git_hooks as cli


def test_install_help_prints_usage_no_install(capsys):
    with patch("metasphere.cli.git_hooks.install_hooks") as m:
        rc = cli.main(["install", "--help"])
    assert rc == 0
    m.assert_not_called()
    out = capsys.readouterr().out
    assert "hooks git" in out
    assert "install" in out


def test_uninstall_help_prints_usage_no_uninstall(capsys):
    with patch("metasphere.cli.git_hooks.uninstall_hooks") as m:
        rc = cli.main(["uninstall", "-h"])
    assert rc == 0
    m.assert_not_called()
    out = capsys.readouterr().out
    assert "uninstall" in out


def test_status_help_prints_usage_no_status(capsys):
    with patch("metasphere.cli.git_hooks.hooks_status") as m:
        rc = cli.main(["status", "--help"])
    assert rc == 0
    m.assert_not_called()


def test_install_flag_shaped_path_rejected(capsys):
    with patch("metasphere.cli.git_hooks.install_hooks") as m:
        rc = cli.main(["install", "--bogus"])
    assert rc == 2
    m.assert_not_called()
    err = capsys.readouterr().err
    assert "--bogus" in err
    assert "flag" in err.lower()


def test_uninstall_flag_shaped_path_rejected(capsys):
    with patch("metasphere.cli.git_hooks.uninstall_hooks") as m:
        rc = cli.main(["uninstall", "-x"])
    assert rc == 2
    m.assert_not_called()


def test_status_flag_shaped_path_rejected(capsys):
    with patch("metasphere.cli.git_hooks.hooks_status") as m:
        rc = cli.main(["status", "--weird"])
    assert rc == 2
    m.assert_not_called()


def test_install_dry_run_still_works(tmp_path):
    # Sanity: real --dry-run positional path still parses correctly
    # after the guard refactor. Uses a real git-init'd repo so
    # install_hooks finds .git/.
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    rc = cli.main(["install", str(repo), "--dry-run"])
    assert rc == 0
    # No shims written under dry-run
    assert not (repo / ".git" / "hooks" / "pre-commit").exists()


def test_install_no_path_uses_cwd(tmp_path, monkeypatch):
    import subprocess

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    monkeypatch.chdir(repo)
    rc = cli.main(["install", "--dry-run"])
    assert rc == 0


def test_top_level_help_unchanged(capsys):
    rc = cli.main(["--help"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "hooks git" in out
