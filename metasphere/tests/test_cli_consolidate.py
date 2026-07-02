"""Tests for ``metasphere consolidate run`` argument hardening.

Same class of bug as the trace.prune / trace.list / tasks.start /
schedule.daemon hardening: a raw ``int(...)`` on user-supplied argv
would throw a ``ValueError`` traceback when the value was flag-shaped,
non-numeric, or missing. The CLI boundary now catches and surfaces a
clean rc=2.

Also asserts a sign-rejection that prevents a future "negative window
silently rotates the threshold past 'now'" footgun on
``--info-archive-after`` and ``--stale-window``.
"""

from __future__ import annotations

import pytest

from metasphere.cli import consolidate as C


# ---------- --stale-window ----------

@pytest.mark.parametrize("bad", ["--bogus", "--unknown", "-x"])
def test_stale_window_rejects_flag_shaped_value(bad, capsys):
    """``consolidate run --stale-window --bogus`` used to detonate inside
    ``int(...)`` with a raw traceback. Now: clean rc=2 + flag-shape
    message."""
    rc = C.main(["run", "--stale-window", bad])
    assert rc == 2
    err = capsys.readouterr().err
    assert "consolidate run" in err
    assert "--stale-window" in err
    assert bad in err


@pytest.mark.parametrize("bad", ["abc", "3.5", ""])
def test_stale_window_rejects_non_int(bad, capsys):
    rc = C.main(["run", "--stale-window", bad])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--stale-window" in err
    assert "integer" in err


def test_stale_window_eq_form_rejects_non_int(capsys):
    """``--stale-window=abc`` hits the alt branch; same guard."""
    rc = C.main(["run", "--stale-window=abc"])
    assert rc == 2
    assert "--stale-window" in capsys.readouterr().err


def test_stale_window_rejects_negative(capsys):
    rc = C.main(["run", "--stale-window", "-5"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--stale-window" in err
    assert "non-negative" in err


def test_stale_window_missing_value(capsys):
    """``--stale-window`` as the final token used to ``IndexError`` off
    the end of argv. Now: clean rc=2."""
    rc = C.main(["run", "--stale-window"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--stale-window" in err
    assert "value" in err


# ---------- --info-archive-after ----------

@pytest.mark.parametrize("bad", ["--bogus", "-x"])
def test_info_archive_after_rejects_flag_shaped_value(bad, capsys):
    rc = C.main(["run", "--info-archive-after", bad])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--info-archive-after" in err


def test_info_archive_after_rejects_non_int(capsys):
    rc = C.main(["run", "--info-archive-after", "abc"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--info-archive-after" in err
    assert "integer" in err


def test_info_archive_after_eq_form_rejects_non_int(capsys):
    rc = C.main(["run", "--info-archive-after=abc"])
    assert rc == 2
    assert "--info-archive-after" in capsys.readouterr().err


def test_info_archive_after_rejects_negative(capsys):
    rc = C.main(["run", "--info-archive-after", "-1"])
    assert rc == 2
    assert "non-negative" in capsys.readouterr().err


def test_info_archive_after_missing_value(capsys):
    rc = C.main(["run", "--info-archive-after"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--info-archive-after" in err


# ---------- --since ----------

def test_since_missing_value(capsys):
    """``--since`` as the final token used to ``IndexError``. The value
    is a free-form string so we don't validate the content, but we do
    refuse to consume past the end of argv."""
    rc = C.main(["run", "--since"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "--since" in err


# ---------- help / usage ----------

def test_help_flag_prints_usage(capsys):
    rc = C.main(["run", "--help"])
    assert rc == 0
    assert "Usage:" in capsys.readouterr().out


def test_top_level_help_prints_usage(capsys):
    rc = C.main(["--help"])
    assert rc == 0
    assert "Usage:" in capsys.readouterr().out


# ---------- happy path ----------

def test_run_dry_run_smoke(capsys, monkeypatch, tmp_path):
    """Sanity: a valid invocation reaches the classifier and prints the
    summary line. Uses a tmp METASPHERE_DIR so no real state is touched."""
    monkeypatch.setenv("METASPHERE_DIR", str(tmp_path))
    rc = C.main(["run", "--dry-run", "--stale-window", "30"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "consolidate" in out
    assert "stale_window=30" in out


def test_run_dry_run_with_info_archive_after(capsys, monkeypatch, tmp_path):
    monkeypatch.setenv("METASPHERE_DIR", str(tmp_path))
    rc = C.main(
        ["run", "--dry-run", "--info-archive-after", "60"]
    )
    assert rc == 0
    assert "consolidate" in capsys.readouterr().out
