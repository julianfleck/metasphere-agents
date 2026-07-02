"""CLI-layer tests for ``metasphere session`` subcommand argv parsing.

Library-level coverage lives in ``test_session.py``; this file
exercises the argv-parsing surface — specifically the flag-shaped
agent-id rejection that mirrors the prior flag-leak audit fixes
(schedule enable/disable, msg reply/done/read/status, tasks
new/assign/move/start/update/done, project init/rename/delete,
project members add/remove, agent spawn/seed).

Pre-fix, ``session info --help`` fell through to ``session_info('--help')``
and printed ``no session: --help`` instead of a usage hint — the
same shape that hid the original ``agent spawn --bogus`` ghost
directories.
"""

from __future__ import annotations

import pytest
from unittest.mock import patch

from metasphere.cli import session as cli


# Every subcommand below routes through a different downstream call,
# so we patch each one individually and assert it never sees the
# flag-shaped value.
_OPS = [
    ("info",    "metasphere.cli.session.session_info"),
    ("attach",  "metasphere.cli.session.attach_to"),
    ("stop",    "metasphere.cli.session.stop_session"),
    ("restart", "metasphere.cli.session.restart_session"),
]


@pytest.mark.parametrize("op,patch_target", _OPS)
@pytest.mark.parametrize("bad", ["--help", "-h", "--bogus", "-x", "--force"])
def test_subcommand_rejects_flag_shaped_agent(op, patch_target, bad, capsys):
    with patch(patch_target) as m:
        rc = cli.main([op, bad])
    assert rc == 2, f"{op} {bad} expected rc=2, got {rc}"
    m.assert_not_called()
    err = capsys.readouterr().err
    assert bad in err
    assert "flag" in err.lower()


@pytest.mark.parametrize("bad", ["--help", "-h", "--bogus", "-x"])
def test_send_rejects_flag_shaped_agent(bad, capsys):
    # ``send`` needs two positionals; supply both so the flag-shape
    # gate (not the arity gate) is what fires.
    with patch("metasphere.cli.session.send_to_session") as m:
        rc = cli.main(["send", bad, "hello"])
    assert rc == 2
    m.assert_not_called()
    err = capsys.readouterr().err
    assert bad in err
    assert "flag" in err.lower()


def test_info_missing_arg_prints_usage(capsys):
    with patch("metasphere.cli.session.session_info") as m:
        rc = cli.main(["info"])
    assert rc == 2
    m.assert_not_called()
    assert "info" in capsys.readouterr().err


def test_send_too_few_args_prints_usage(capsys):
    with patch("metasphere.cli.session.send_to_session") as m:
        rc = cli.main(["send", "@agent"])
    assert rc == 2
    m.assert_not_called()
    assert "send" in capsys.readouterr().err


def test_top_level_help_prints_usage(capsys):
    rc = cli.main(["--help"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "session" in out and "attach" in out


def test_real_agent_id_passes_through(capsys):
    # @-prefixed names that don't start with ``-`` reach the downstream
    # call. Mock returns None so the handler doesn't actually hit tmux.
    with patch("metasphere.cli.session.session_info", return_value=None) as m:
        rc = cli.main(["info", "@foo"])
    assert rc == 1
    m.assert_called_once_with("@foo")


@pytest.mark.parametrize("argv,extra", [
    (["list", "--bogus"], "--bogus"),
    (["list", "extra"], "extra"),
])
def test_list_rejects_unknown_args(capsys, argv, extra):
    """``session list`` takes no arguments; pre-hardening silently
    dropped any extras and printed the full session table anyway."""
    with patch("metasphere.cli.session.list_sessions") as m:
        rc = cli.main(argv)
    assert rc == 2
    m.assert_not_called()
    _, err = capsys.readouterr()
    assert "session list" in err
    assert extra in err


@pytest.mark.parametrize("extra", ["--bogus", "trailing-positional"])
def test_info_rejects_trailing_arg_after_agent(capsys, extra):
    """``session info @x --bogus`` previously dropped ``--bogus`` and
    proceeded with the lookup; the wake-side fix in 27dccc4 added the
    same shape of guard for ``agent wake``."""
    with patch("metasphere.cli.session.session_info") as m:
        rc = cli.main(["info", "@foo", extra])
    assert rc == 2
    m.assert_not_called()
    _, err = capsys.readouterr()
    assert "session info" in err
    assert extra in err
