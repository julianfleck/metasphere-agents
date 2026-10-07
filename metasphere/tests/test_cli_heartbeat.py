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


@pytest.mark.parametrize(
    "args, unexpected",
    [
        (["once", "extra"], "extra"),
        (["check", "--bogus"], "--bogus"),
        (["daemon", "300", "extra"], "extra"),
        (["daemon", "300", "--bogus"], "--bogus"),
    ],
)
def test_surplus_arguments_are_rejected_before_dispatch(args, unexpected, capsys):
    with (
        patch("metasphere.cli.heartbeat.heartbeat_once") as once,
        patch("metasphere.cli.heartbeat.heartbeat_daemon") as daemon,
    ):
        rc = cli.main(args)

    assert rc == 2
    once.assert_not_called()
    daemon.assert_not_called()
    err = capsys.readouterr().err
    assert unexpected in err
    assert "unexpected" in err.lower()


def test_duplicate_invoke_agent_flag_is_rejected_before_dispatch(capsys):
    with patch("metasphere.cli.heartbeat.heartbeat_once") as once:
        rc = cli.main(["once", "--invoke-agent", "--invoke-agent"])

    assert rc == 2
    once.assert_not_called()
    err = capsys.readouterr().err
    assert "--invoke-agent" in err
    assert "more than once" in err


@pytest.mark.parametrize(
    "args, expected_invoke",
    [
        ([], False),
        (["once"], False),
        (["check", "--invoke-agent"], True),
        (["--invoke-agent", "once"], True),
    ],
)
def test_one_shot_exact_forms_dispatch(args, expected_invoke, monkeypatch):
    monkeypatch.delenv("HEARTBEAT_INVOKE_AGENT", raising=False)
    with patch("metasphere.cli.heartbeat.heartbeat_once") as once:
        rc = cli.main(args)

    assert rc == 0
    once.assert_called_once()
    _, kwargs = once.call_args
    assert kwargs["invoke_agent"] is expected_invoke


def test_daemon_accepts_invoke_agent_before_interval():
    with patch("metasphere.cli.heartbeat.heartbeat_daemon") as daemon:
        rc = cli.main(["daemon", "--invoke-agent", "30"])

    assert rc == 0
    daemon.assert_called_once()
    _, kwargs = daemon.call_args
    assert kwargs["interval_seconds"] == 30
    assert kwargs["invoke_agent"] is True
