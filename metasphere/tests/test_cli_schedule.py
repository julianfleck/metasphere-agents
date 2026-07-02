"""CLI-layer tests for ``metasphere schedule``.

Library-level coverage lives in ``test_schedule.py``; this file
exercises the argv-parsing surface — specifically the flag-shaped
job-ref rejection added after df6812e / 206c14b. Without these
guards, ``schedule enable --help`` fell through to
``set_enabled('--help', ...)`` and printed ``job not found: --help``
instead of usage.
"""

from __future__ import annotations

from unittest.mock import patch

from metasphere.cli import schedule as cli


def test_enable_help_prints_usage_no_lookup(capsys):
    with patch("metasphere.schedule.set_enabled") as m:
        rc = cli.main(["enable", "--help"])
    assert rc == 0
    m.assert_not_called()
    out = capsys.readouterr().out
    assert "schedule" in out and "enable" in out


def test_disable_help_prints_usage_no_lookup(capsys):
    with patch("metasphere.schedule.set_enabled") as m:
        rc = cli.main(["disable", "-h"])
    assert rc == 0
    m.assert_not_called()
    out = capsys.readouterr().out
    assert "disable" in out


def test_enable_flag_shaped_ref_rejected(capsys):
    with patch("metasphere.schedule.set_enabled") as m:
        rc = cli.main(["enable", "--force"])
    assert rc == 2
    m.assert_not_called()
    err = capsys.readouterr().err
    assert "--force" in err
    assert "flag" in err.lower()


def test_disable_flag_shaped_ref_rejected(capsys):
    with patch("metasphere.schedule.set_enabled") as m:
        rc = cli.main(["disable", "-x"])
    assert rc == 2
    m.assert_not_called()


def test_enable_missing_arg_prints_usage(capsys):
    with patch("metasphere.schedule.set_enabled") as m:
        rc = cli.main(["enable"])
    assert rc == 2
    m.assert_not_called()
    err = capsys.readouterr().err
    assert "enable" in err


def test_enable_real_job_id_still_dispatches():
    with patch("metasphere.schedule.set_enabled", return_value=True) as m:
        rc = cli.main(["enable", "metasphere-auto-update"])
    assert rc == 0
    m.assert_called_once()
    args, kwargs = m.call_args
    assert args[0] == "metasphere-auto-update"
    assert args[1] is True


def test_top_level_help_unchanged(capsys):
    rc = cli.main(["--help"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "schedule" in out


# ---------- daemon argv hardening ----------
#
# Same class as the consolidate run / trace prune / trace list /
# session info hardening: a raw ``int(argv[0])`` on user-supplied argv
# detonated on flag-shaped values, and a negative interval would
# propagate to ``time.sleep`` and crash the daemon loop on the first
# tick. The CLI boundary now catches and surfaces a clean rc=2.

import pytest


def test_daemon_help_prints_usage(capsys):
    rc = cli.main(["daemon", "--help"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "schedule" in out
    assert "daemon" in out


def test_daemon_short_help_prints_usage(capsys):
    rc = cli.main(["daemon", "-h"])
    assert rc == 0
    assert "daemon" in capsys.readouterr().out


@pytest.mark.parametrize("bad", ["--bogus", "--unknown", "-x"])
def test_daemon_rejects_flag_shaped_interval(bad, capsys):
    rc = cli.main(["daemon", bad])
    assert rc == 2
    err = capsys.readouterr().err
    assert "schedule daemon" in err
    assert bad in err
    assert "flag" in err.lower()


@pytest.mark.parametrize("bad", ["abc", "3.5", ""])
def test_daemon_rejects_non_int(bad, capsys):
    rc = cli.main(["daemon", bad])
    assert rc == 2
    err = capsys.readouterr().err
    assert "schedule daemon" in err
    assert "integer" in err


def test_daemon_rejects_negative_interval(capsys):
    rc = cli.main(["daemon", "-5"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "schedule daemon" in err
    assert "non-negative" in err


@pytest.mark.parametrize("argv,extra", [
    (["list", "--bogus"], "--bogus"),
    (["list", "widget", "--bogus"], "--bogus"),
    (["list", "widget", "extra-positional"], "extra-positional"),
])
def test_list_rejects_unknown_args(capsys, argv, extra):
    """``schedule list`` previously took ``rest[0]`` as project_filter
    if not flag-shaped, and silently dropped everything else (incl.
    ``--bogus``). Now rc=2 surfaces the typo before listing.
    """
    rc = cli.main(argv)
    _, err = capsys.readouterr()
    assert rc == 2
    assert "schedule list" in err
    assert extra in err
