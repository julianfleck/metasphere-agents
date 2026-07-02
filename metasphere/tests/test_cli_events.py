"""Tests for the ``metasphere events`` CLI (tail + prune argument hardening)."""

from __future__ import annotations

import datetime as dt
import json

import pytest

from metasphere.cli import events as E


def _write_dated(events_dir, date_obj, msg):
    events_dir.mkdir(parents=True, exist_ok=True)
    rec = json.dumps({
        "id": f"evt-{msg}",
        "timestamp": f"{date_obj.isoformat()}T12:00:00Z",
        "type": "cli.test",
        "message": msg,
        "agent": "@x",
        "scope": "/",
        "meta": {},
    }) + "\n"
    path = events_dir / f"events-{date_obj.isoformat()}.jsonl"
    path.write_text(rec)
    return path


# ---------- top-level / help ----------

def test_no_args_prints_usage_rc2(capsys):
    rc = E.main([])
    assert rc == 2
    assert "Usage:" in capsys.readouterr().err


def test_help_flag_prints_usage(capsys):
    rc = E.main(["--help"])
    assert rc == 0
    assert "Usage:" in capsys.readouterr().out


def test_unknown_subcommand_rc2(capsys):
    rc = E.main(["bogus"])
    assert rc == 2
    assert "unknown subcommand" in capsys.readouterr().err


# ---------- tail ----------

def test_tail_outputs_events(tmp_paths, monkeypatch, capsys):
    monkeypatch.setattr("metasphere.cli.events.resolve", lambda: tmp_paths)
    _write_dated(tmp_paths.root / "events", dt.date.today(), "hello-tail")
    rc = E.main(["tail"])
    assert rc == 0
    assert "hello-tail" in capsys.readouterr().out


def test_tail_rejects_bad_limit(tmp_paths, monkeypatch, capsys):
    monkeypatch.setattr("metasphere.cli.events.resolve", lambda: tmp_paths)
    rc = E.main(["tail", "--limit", "abc"])
    assert rc == 2
    assert "--limit" in capsys.readouterr().err


# ---------- prune argument hardening ----------

@pytest.mark.parametrize("bad", ["--bogus", "-x"])
def test_prune_rejects_unknown_flag(bad, capsys):
    rc = E.main(["prune", bad])
    assert rc == 2
    err = capsys.readouterr().err
    assert "events prune" in err


def test_prune_help_prints_usage(capsys):
    assert E.main(["prune", "--help"]) == 0
    assert "Usage:" in capsys.readouterr().out


def test_prune_missing_days_rc2(capsys):
    rc = E.main(["prune"])
    assert rc == 2
    assert "events prune" in capsys.readouterr().err


@pytest.mark.parametrize("bad", ["abc", "3.5"])
def test_prune_rejects_non_int_days(bad, capsys):
    rc = E.main(["prune", bad])
    assert rc == 2
    assert "expects an integer" in capsys.readouterr().err


def test_prune_rejects_negative_days(capsys):
    rc = E.main(["prune", "-5"])
    # ``-5`` is digit-shaped after stripping ``-``; reaches the negativity
    # guard rather than the unknown-flag branch.
    assert rc == 2
    assert "non-negative" in capsys.readouterr().err


# ---------- prune behaviour (delete / compress / dry-run) ----------

def test_prune_delete_reports_count(tmp_paths, monkeypatch, capsys):
    monkeypatch.setattr("metasphere.cli.events.resolve", lambda: tmp_paths)
    events_dir = tmp_paths.root / "events"
    old = _write_dated(events_dir, dt.date.today() - dt.timedelta(days=30), "old")
    rc = E.main(["prune", "2"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "delete 1 day-file(s)" in out
    assert not old.exists()


def test_prune_dry_run_keeps_files(tmp_paths, monkeypatch, capsys):
    monkeypatch.setattr("metasphere.cli.events.resolve", lambda: tmp_paths)
    events_dir = tmp_paths.root / "events"
    old = _write_dated(events_dir, dt.date.today() - dt.timedelta(days=30), "old")
    rc = E.main(["prune", "2", "--dry-run"])
    assert rc == 0
    assert "would delete" in capsys.readouterr().out
    assert old.exists()


def test_prune_compress_reports_and_replaces(tmp_paths, monkeypatch, capsys):
    monkeypatch.setattr("metasphere.cli.events.resolve", lambda: tmp_paths)
    events_dir = tmp_paths.root / "events"
    old = _write_dated(events_dir, dt.date.today() - dt.timedelta(days=30), "old")
    rc = E.main(["prune", "2", "--compress"])
    assert rc == 0
    assert "compress 1 day-file(s)" in capsys.readouterr().out
    assert not old.exists()
    assert (events_dir / (old.name + ".gz")).exists()
