"""Tests for ``metasphere status`` CLI shim (arg handling).

``metasphere.cli.status:main`` is the CLI entry point that wraps
``metasphere.status:summary``. Tests for the rendering logic itself
live in ``test_status.py``; this file covers argv handling only.
"""

from __future__ import annotations

import pytest

from metasphere.cli import status as cli_status


def test_status_help_returns_zero(capsys):
    rc = cli_status.main(["--help"])
    out, _ = capsys.readouterr()
    assert rc == 0
    assert "Usage: metasphere status" in out


def test_status_short_help_returns_zero(capsys):
    rc = cli_status.main(["-h"])
    out, _ = capsys.readouterr()
    assert rc == 0
    assert "Usage: metasphere status" in out


@pytest.mark.parametrize(
    "argv,extra",
    [
        (["--bogus"], "--bogus"),
        (["extra"], "extra"),
        (["--filter=foo"], "--filter=foo"),
    ],
)
def test_status_rejects_unknown_args(tmp_paths, capsys, argv, extra):
    """``metasphere status`` takes no arguments. Previously silently
    dropped extras and rendered the full summary anyway. Now rc=2."""
    rc = cli_status.main(argv)
    out, err = capsys.readouterr()
    assert rc == 2
    assert out == ""
    assert "metasphere status:" in err
    assert extra in err
