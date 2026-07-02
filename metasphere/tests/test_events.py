"""Tests for ``metasphere.events`` — the append-only events log.

Covers the JSONL record schema, ``log_event`` field defaults, the
``tail_events`` reader, and concurrent-write safety (multiple processes
appending to the same daily log without torn lines).
"""

import datetime as dt
import gzip
import json
import multiprocessing as mp
import re
import shutil
import subprocess

import pytest

from metasphere.events import log_event, prune_events, tail_events


def _worker(i):
    from metasphere.events import log_event
    log_event("test", f"msg-{i}", agent="@w", meta={"i": i})


def test_log_event_schema(tmp_paths):
    rec = log_event("boot", "hello", agent="@x", meta={"k": 1})
    # Required schema fields: id, timestamp, type, message, agent, scope, meta
    assert set(rec.keys()) >= {
        "id", "timestamp", "type", "message", "agent", "scope", "meta",
    }
    assert rec["type"] == "boot"
    assert rec["message"] == "hello"
    assert rec["agent"] == "@x"
    assert rec["meta"] == {"k": 1}
    assert rec["id"].startswith("evt-")
    assert rec["timestamp"].endswith("Z")
    assert rec["scope"]  # non-empty

    line = tmp_paths.events_log.read_text().strip()
    parsed = json.loads(line)
    assert parsed["id"] == rec["id"]
    assert parsed["timestamp"] == rec["timestamp"]
    assert parsed["scope"] == rec["scope"]


def test_log_event_defaults_meta_and_agent(tmp_paths, monkeypatch):
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@defaulted")
    rec = log_event("tick", "no-meta")
    assert rec["meta"] == {}
    assert rec["agent"] == "@defaulted"


def test_log_event_concurrent(tmp_paths):
    ctx = mp.get_context("fork")
    procs = [ctx.Process(target=_worker, args=(i,)) for i in range(20)]
    for p in procs:
        p.start()
    for p in procs:
        p.join()
    lines = tmp_paths.events_log.read_text().strip().splitlines()
    assert len(lines) == 20
    parsed = [json.loads(l) for l in lines]
    assert sorted(p["meta"]["i"] for p in parsed) == list(range(20))
    # All have the required fields.
    for p in parsed:
        assert {"id", "timestamp", "type", "message", "agent", "scope", "meta"} <= set(p.keys())


def test_log_event_writes_to_dated_file(tmp_paths):
    # Daily rotation: log_event must land in a date-stamped file under
    # the events dir, never the legacy single events.jsonl.
    log_event("rotation", "dated", agent="@x")
    events_dir = tmp_paths.root / "events"
    dated = sorted(events_dir.glob("events-*.jsonl"))
    assert len(dated) == 1
    assert re.fullmatch(r"events-\d{4}-\d{2}-\d{2}\.jsonl", dated[0].name)
    # Legacy single file must NOT be created by the new code path.
    assert not (events_dir / "events.jsonl").exists()


def test_tail_events_reads_across_dated_files(tmp_paths):
    # Synthesize two dated files (yesterday + today) and assert tail_events
    # walks both, returning lines in chronological order.
    events_dir = tmp_paths.root / "events"
    events_dir.mkdir(parents=True, exist_ok=True)

    def _rec(ts: str, msg: str) -> str:
        return json.dumps({
            "id": f"evt-{msg}",
            "timestamp": ts,
            "type": "rot.test",
            "message": msg,
            "agent": "@x",
            "scope": "/",
            "meta": {},
        })

    yday = events_dir / "events-2026-04-10.jsonl"
    today = events_dir / "events-2026-04-11.jsonl"
    yday.write_text(_rec("2026-04-10T23:59:00Z", "yday-1") + "\n"
                    + _rec("2026-04-10T23:59:30Z", "yday-2") + "\n")
    today.write_text(_rec("2026-04-11T00:00:01Z", "today-1") + "\n"
                     + _rec("2026-04-11T00:00:02Z", "today-2") + "\n")

    out = tail_events(n=4, paths=tmp_paths)
    lines = out.splitlines()
    assert len(lines) == 4
    # Chronological order: yday rows first, today rows last.
    assert "yday-1" in lines[0]
    assert "yday-2" in lines[1]
    assert "today-1" in lines[2]
    assert "today-2" in lines[3]

    # n smaller than total: should return only the most recent rows.
    out2 = tail_events(n=2, paths=tmp_paths)
    lines2 = out2.splitlines()
    assert len(lines2) == 2
    assert "today-1" in lines2[0]
    assert "today-2" in lines2[1]


def test_tail_events_legacy_fallback(tmp_paths):
    # Transition guard: when no dated files exist but a legacy events.jsonl
    # is present, tail_events still reads it. This keeps freshly-installed
    # hosts and existing fixtures working until they roll over.
    events_dir = tmp_paths.root / "events"
    events_dir.mkdir(parents=True, exist_ok=True)
    legacy = events_dir / "events.jsonl"
    legacy.write_text(json.dumps({
        "id": "evt-legacy",
        "timestamp": "2025-12-31T12:00:00Z",
        "type": "legacy.tick",
        "message": "from-legacy",
        "agent": "@x",
        "scope": "/",
        "meta": {},
    }) + "\n")
    out = tail_events(n=5, paths=tmp_paths)
    assert "from-legacy" in out


def test_log_event_jq_roundtrip(tmp_paths):
    jq = shutil.which("jq")
    if not jq:
        pytest.skip("jq not installed")
    log_event("jq.test", "hello jq", agent="@x", meta={"n": 7})
    out = subprocess.check_output(
        [jq, "-r", ".timestamp + \"|\" + .id + \"|\" + .type + \"|\" + .agent + \"|\" + .scope",
         str(tmp_paths.events_log)],
        text=True,
    ).strip().splitlines()[-1]
    ts, eid, typ, agent, scope = out.split("|")
    assert ts and ts != "null"
    assert eid.startswith("evt-")
    assert typ == "jq.test"
    assert agent == "@x"
    assert scope and scope != "null"


# --- prune_events retention --------------------------------------------------

def _write_dated(events_dir, date_obj, msg, *, gz=False):
    """Write a one-line dated event file for ``date_obj``; return the path."""
    events_dir.mkdir(parents=True, exist_ok=True)
    rec = json.dumps({
        "id": f"evt-{msg}",
        "timestamp": f"{date_obj.isoformat()}T12:00:00Z",
        "type": "ret.test",
        "message": msg,
        "agent": "@x",
        "scope": "/",
        "meta": {},
    }) + "\n"
    name = f"events-{date_obj.isoformat()}.jsonl" + (".gz" if gz else "")
    path = events_dir / name
    if gz:
        with gzip.open(path, "wt", encoding="utf-8") as f:
            f.write(rec)
    else:
        path.write_text(rec)
    return path


def test_prune_events_delete(tmp_paths):
    events_dir = tmp_paths.root / "events"
    today = dt.date.today()
    old = _write_dated(events_dir, today - dt.timedelta(days=100), "old")
    recent = _write_dated(events_dir, today - dt.timedelta(days=1), "recent")
    live = _write_dated(events_dir, today, "live")

    res = prune_events(2, paths=tmp_paths)
    assert res.deleted == [old.name]
    assert res.bytes_reclaimed > 0
    assert res.dry_run is False
    # Strictly-older file gone; recent + live retained.
    assert not old.exists()
    assert recent.exists()
    assert live.exists()


def test_prune_events_compress_is_reversible(tmp_paths):
    events_dir = tmp_paths.root / "events"
    today = dt.date.today()
    old = _write_dated(events_dir, today - dt.timedelta(days=30), "compressme")

    res = prune_events(2, compress=True, paths=tmp_paths)
    assert res.compressed == [old.name]
    assert not res.deleted
    # Original replaced by a .gz holding the same record, losslessly.
    assert not old.exists()
    gz = events_dir / (old.name + ".gz")
    assert gz.exists()
    with gzip.open(gz, "rt", encoding="utf-8") as f:
        assert "compressme" in f.read()
    # tail still surfaces compressed history transparently.
    assert "compressme" in tail_events(n=5, paths=tmp_paths)


def test_prune_events_dry_run_touches_nothing(tmp_paths):
    events_dir = tmp_paths.root / "events"
    today = dt.date.today()
    old = _write_dated(events_dir, today - dt.timedelta(days=50), "kept")

    res = prune_events(2, dry_run=True, paths=tmp_paths)
    assert res.deleted == [old.name]
    assert res.dry_run is True
    # Nothing actually removed.
    assert old.exists()


def test_prune_events_retains_today_at_zero_days(tmp_paths):
    events_dir = tmp_paths.root / "events"
    live = _write_dated(events_dir, dt.date.today(), "live")
    res = prune_events(0, paths=tmp_paths)
    assert res.deleted == []
    assert live.exists()


def test_prune_events_skips_already_compressed(tmp_paths):
    events_dir = tmp_paths.root / "events"
    today = dt.date.today()
    gz = _write_dated(events_dir, today - dt.timedelta(days=40), "already", gz=True)
    res = prune_events(2, compress=True, paths=tmp_paths)
    # Already-compressed file is left untouched, not re-listed.
    assert res.compressed == []
    assert gz.exists()


def test_prune_events_delete_removes_compressed_too(tmp_paths):
    events_dir = tmp_paths.root / "events"
    today = dt.date.today()
    gz = _write_dated(events_dir, today - dt.timedelta(days=40), "oldgz", gz=True)
    res = prune_events(2, paths=tmp_paths)
    # Delete mode reclaims compressed history as well.
    assert res.deleted == [gz.name]
    assert not gz.exists()
