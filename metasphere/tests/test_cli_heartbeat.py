"""CLI-layer tests for ``metasphere heartbeat``.

Library-level coverage lives in ``test_heartbeat.py``; this file
exercises the argv-parsing surface of the ``daemon`` subcommand —
specifically the flag-shaped / non-int / negative interval rejection.

Same class as the schedule.daemon and consolidate.run hardening: a
raw ``int(args[1])`` detonated on flag-shaped values, and a negative
interval would propagate to ``time.sleep`` inside the daemon loop
and crash the first tick. The CLI boundary now catches and surfaces
a clean rc=2.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from metasphere.cli import heartbeat as cli


def test_top_level_help_prints_usage(capsys):
    rc = cli.main(["--help"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "heartbeat" in out


def test_daemon_help_prints_usage(capsys):
    with patch("metasphere.cli.heartbeat.heartbeat_daemon") as m:
        rc = cli.main(["daemon", "--help"])
    assert rc == 0
    m.assert_not_called()
    out = capsys.readouterr().out
    assert "heartbeat" in out
    assert "daemon" in out


def test_daemon_short_help_prints_usage(capsys):
    with patch("metasphere.cli.heartbeat.heartbeat_daemon") as m:
        rc = cli.main(["daemon", "-h"])
    assert rc == 0
    m.assert_not_called()


@pytest.mark.parametrize("bad", ["--bogus", "--unknown", "-x"])
def test_daemon_rejects_flag_shaped_interval(bad, capsys):
    with patch("metasphere.cli.heartbeat.heartbeat_daemon") as m:
        rc = cli.main(["daemon", bad])
    assert rc == 2
    m.assert_not_called()
    err = capsys.readouterr().err
    assert "heartbeat daemon" in err
    assert bad in err
    assert "flag" in err.lower()


@pytest.mark.parametrize("bad", ["abc", "3.5", ""])
def test_daemon_rejects_non_int(bad, capsys):
    with patch("metasphere.cli.heartbeat.heartbeat_daemon") as m:
        rc = cli.main(["daemon", bad])
    assert rc == 2
    m.assert_not_called()
    err = capsys.readouterr().err
    assert "heartbeat daemon" in err
    assert "integer" in err


def test_daemon_rejects_negative_interval(capsys):
    with patch("metasphere.cli.heartbeat.heartbeat_daemon") as m:
        rc = cli.main(["daemon", "-5"])
    assert rc == 2
    m.assert_not_called()
    err = capsys.readouterr().err
    assert "heartbeat daemon" in err
    assert "non-negative" in err


def test_daemon_valid_interval_dispatches():
    """Happy path: ``heartbeat daemon 0`` passes argv through to the
    library entry. interval=0 keeps the test fast (no real sleep) while
    confirming non-negative integers reach the dispatcher."""
    with patch("metasphere.cli.heartbeat.heartbeat_daemon") as m:
        rc = cli.main(["daemon", "0"])
    assert rc == 0
    m.assert_called_once()
    _, kwargs = m.call_args
    assert kwargs["interval_seconds"] == 0
