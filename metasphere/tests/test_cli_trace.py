"""Tests for ``metasphere trace`` CLI argument hardening."""

from __future__ import annotations

import pytest

from metasphere.cli import trace as T


# ---------- trace prune <days> ----------

@pytest.mark.parametrize("bad", ["--bogus", "--unknown", "-x"])
def test_prune_rejects_flag_shaped_days(bad, capsys):
    """``trace prune --bogus`` used to detonate inside ``int(...)`` with
    a raw ``ValueError`` traceback. Now surfaces a clean rc=2 with a
    message pointing at the flag-shape, matching the schedule.enable /
    spawn-name guards already in place across the CLI surface."""
    rc = T.main(["prune", bad])
    assert rc == 2
    err = capsys.readouterr().err
    assert "trace prune" in err
    assert bad in err


def test_prune_help_flag_prints_usage(capsys):
    rc = T.main(["prune", "--help"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Usage:" in out
    assert "trace" in out


def test_prune_help_short_flag_prints_usage(capsys):
    rc = T.main(["prune", "-h"])
    assert rc == 0
    assert "Usage:" in capsys.readouterr().out


@pytest.mark.parametrize("bad", ["abc", "3.5", ""])
def test_prune_rejects_non_int_days(bad, capsys):
    """Non-integer day counts get a clean rejection instead of a
    ValueError traceback."""
    rc = T.main(["prune", bad])
    assert rc == 2
    err = capsys.readouterr().err
    assert "trace prune" in err


def test_prune_rejects_negative_days(capsys):
    """``prune -5`` would set the cutoff to today+5, classifying every
    trace dir as 'older than cutoff' and wiping the whole tree. Reject
    at the CLI boundary."""
    # Note: ``-5`` is flag-shaped, but ``.lstrip("-").isdigit()`` lets
    # it through the flag-shape check so we can give a more precise
    # "must be non-negative" message.
    rc = T.main(["prune", "-5"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "non-negative" in err


def test_prune_missing_days_prints_usage(capsys):
    rc = T.main(["prune"])
    assert rc == 2
    assert "usage:" in capsys.readouterr().err


def test_prune_valid_days_returns_zero(capsys):
    """Sanity: a valid integer reaches ``prune_traces`` and returns 0."""
    rc = T.main(["prune", "9999"])
    assert rc == 0
    assert "removed" in capsys.readouterr().out


# ---------- trace list --limit ----------

def test_list_rejects_non_int_limit(capsys):
    """``trace list --limit abc`` used to traceback inside int().
    Now surfaces a clean rc=2 + message."""
    rc = T.main(["list", "--limit", "abc"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "trace list" in err
    assert "--limit" in err


def test_list_rejects_short_limit_non_int(capsys):
    rc = T.main(["list", "-n", "abc"])
    assert rc == 2
    assert "--limit" in capsys.readouterr().err


def test_list_valid_limit_runs(capsys):
    """Smoke: a valid --limit value doesn't trip the new guard."""
    rc = T.main(["list", "--limit", "5"])
    assert rc == 0


@pytest.mark.parametrize("argv,extra", [
    (["list", "--bogus"], "--bogus"),
    (["list", "--errors", "--bogus"], "--bogus"),
    (["list", "extra-positional"], "extra-positional"),
])
def test_list_rejects_unknown_args(capsys, argv, extra):
    """``trace list`` previously silently dropped unknown flags/args
    (the parse loop ``else: i += 1`` branch). Now rc=2."""
    rc = T.main(argv)
    _, err = capsys.readouterr()
    assert rc == 2
    assert "trace list" in err
    assert extra in err
