"""Coverage for ``metasphere docs`` error paths and ``--output``.

The happy-path `--check` flow is covered by ``test_cli_help.py``
(``test_docs_check_passes_against_committed_reference``). This module
fills in the gaps: write mode with an explicit output path, the stale-
file branch of ``--check``, ``--output`` argument-handling, and the
unknown-flag fallthrough.
"""

from __future__ import annotations

from pathlib import Path

from metasphere.cli import docs as _docs


def test_docs_writes_to_explicit_output(tmp_path, capsys):
    out = tmp_path / "subdir" / "CLI.md"
    rc = _docs.main(["--output", str(out)])
    assert rc == 0
    assert out.exists()
    body = out.read_text()
    # Rendered doc must include at least the top-level marker the
    # registry emits + a known subcommand name.
    assert body.strip()
    assert "agent" in body
    captured = capsys.readouterr()
    assert str(out) in captured.out


def test_docs_output_equals_form(tmp_path, capsys):
    out = tmp_path / "CLI.md"
    rc = _docs.main([f"--output={out}"])
    assert rc == 0
    assert out.exists()
    assert out.read_text().strip()


def test_docs_output_missing_value_exits_2(tmp_path, capsys):
    rc = _docs.main(["--output"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--output needs a path" in err


def test_docs_unknown_flag_exits_2(capsys):
    rc = _docs.main(["--bogus"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "unknown arg: --bogus" in err
    # Usage prints alongside the error.
    assert "Usage:" in err


def test_docs_check_detects_stale_file(tmp_path, capsys):
    stale = tmp_path / "CLI.md"
    stale.write_text("definitely not the real rendered docs\n")
    rc = _docs.main(["--check", "--output", str(stale)])
    assert rc == 1
    err = capsys.readouterr().err
    assert "stale" in err
    assert str(stale) in err


def test_docs_check_missing_file_is_stale(tmp_path, capsys):
    out = tmp_path / "does-not-exist.md"
    rc = _docs.main(["--check", "--output", str(out)])
    assert rc == 1
    assert "stale" in capsys.readouterr().err


def test_default_output_resolves_from_cwd_when_in_repo(tmp_path, monkeypatch):
    # Simulate a worktree: a checkout carrying the same fingerprint
    # as the source repo (``pyproject.toml`` + ``metasphere/__init__.py``)
    # but at a different path than the editable-installed package.
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "metasphere"\n')
    (tmp_path / "metasphere").mkdir()
    (tmp_path / "metasphere" / "__init__.py").write_text("")

    monkeypatch.chdir(tmp_path)
    assert _docs._default_output() == tmp_path / "docs" / "CLI.md"


def test_default_output_falls_back_to_package_root_outside_repo(tmp_path, monkeypatch):
    # cwd outside any metasphere-agents checkout: default output
    # must fall back to the editable-installed package's repo root
    # so the command keeps working from arbitrary directories.
    monkeypatch.chdir(tmp_path)
    expected = _docs._repo_root() / "docs" / "CLI.md"
    assert _docs._default_output() == expected


def test_docs_writes_to_worktree_when_invoked_from_worktree(tmp_path, monkeypatch, capsys):
    # End-to-end: running ``metasphere docs`` from inside a fake
    # worktree must materialize ``<worktree>/docs/CLI.md`` rather
    # than touching the installed package's checkout.
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "metasphere"\n')
    (tmp_path / "metasphere").mkdir()
    (tmp_path / "metasphere" / "__init__.py").write_text("")

    monkeypatch.chdir(tmp_path)
    rc = _docs.main([])
    assert rc == 0
    assert (tmp_path / "docs" / "CLI.md").exists()
    assert (tmp_path / "docs" / "CLI.md").read_text().strip()
