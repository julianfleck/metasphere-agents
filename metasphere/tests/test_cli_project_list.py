"""CLI-layer tests for ``metasphere project list`` argv handling.

Library-level coverage for ``list_projects`` lives in
``test_project.py``; this file targets the CLI shim's
unknown-arg rejection added in the read-side flag-leak audit
close-out.
"""

from __future__ import annotations

import pytest

from metasphere.cli import project as cli


@pytest.mark.parametrize("argv,extra", [
    (["list", "--bogus"], "--bogus"),
    (["list", "extra"], "extra"),
])
def test_project_list_rejects_unknown_args(tmp_paths, capsys, argv, extra):
    """``project list`` takes no arguments; pre-hardening silently
    dropped extras and rendered the full project table anyway."""
    rc = cli.main(argv)
    _, err = capsys.readouterr()
    assert rc == 2
    assert "project list" in err
    assert extra in err
