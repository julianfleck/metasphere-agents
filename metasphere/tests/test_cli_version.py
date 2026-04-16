"""Tests for ``metasphere version`` CLI."""

from __future__ import annotations

from unittest import mock

from metasphere.cli import version as V


def test_version_prints_version_and_commit(capsys):
    with mock.patch.object(V, "_head_hash", return_value="abcdef123456"):
        rc = V.main([])
    out, _ = capsys.readouterr()
    assert rc == 0
    lines = out.strip().splitlines()
    assert len(lines) == 2
    assert lines[0].startswith("metasphere ")
    assert lines[1] == "commit: abcdef123456"


def test_version_unknown_commit_when_not_a_repo(capsys):
    with mock.patch.object(V, "_head_hash", return_value="(unknown)"):
        rc = V.main([])
    out, _ = capsys.readouterr()
    assert "commit: (unknown)" in out
    assert rc == 0


def test_version_registered_in_dispatcher():
    from metasphere.cli.main import REGISTRY
    assert "version" in REGISTRY
    assert REGISTRY["version"] == "metasphere.cli.version:main"
