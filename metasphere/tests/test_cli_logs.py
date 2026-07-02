"""Tests for ``metasphere logs``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from metasphere.cli import logs as L


def test_service_path_resolves_gateway_to_logs_dir(tmp_paths):
    p = L._service_path("gateway", tmp_paths)
    assert p == tmp_paths.logs / "gateway.log"


def test_service_path_resolves_reaper_to_logs_dir(tmp_paths):
    # reaper.log is grep-friendly after e792a9c (only actionable runs
    # append). Operators reach it via ``metasphere logs reaper`` just
    # like the persistent daemons.
    p = L._service_path("reaper", tmp_paths)
    assert p == tmp_paths.logs / "reaper.log"


def test_service_path_resolves_posthook_to_suppressions_log(tmp_paths):
    # posthook.py writes intentional fail-close suppressions to
    # posthook-suppressions.log. The CLI alias is ``posthook`` (matches
    # the subsystem, not the filename) so operators don't have to
    # remember the hyphenated suffix.
    p = L._service_path("posthook", tmp_paths)
    assert p == tmp_paths.logs / "posthook-suppressions.log"


def test_service_path_resolves_update_to_auto_update_log(tmp_paths):
    # metasphere.update writes to auto-update.log (LOG_FILENAME in
    # update.py). The CLI alias is ``update`` to match the subsystem
    # name (``metasphere update`` is the opt-in upgrade entrypoint),
    # not the on-disk filename.
    p = L._service_path("update", tmp_paths)
    assert p == tmp_paths.logs / "auto-update.log"


def test_service_path_events_routes_to_dated_jsonl(tmp_paths):
    p = L._service_path("events", tmp_paths)
    assert "events-" in p.name
    assert p.suffix == ".jsonl"


def test_tail_lines_returns_last_n(tmp_path):
    f = tmp_path / "log.txt"
    f.write_text("\n".join(f"line-{i}" for i in range(100)) + "\n")
    tail = L._tail_lines(f, 10)
    assert len(tail) == 10
    # Last line is either 'line-99\n' (if file ends with \n then no
    # trailing empty string depending on readlines semantics).
    assert "line-99" in "".join(tail)


def test_tail_lines_file_missing(tmp_path):
    assert L._tail_lines(tmp_path / "nope.log", 5) == []


def test_prettify_events_line_formats_json():
    rec = {"timestamp": "2026-04-15T12:00:00Z", "type": "task.consolidate",
           "agent": "@orchestrator", "message": "hi", "meta": {"a": 1}}
    out = L._prettify_events_line(json.dumps(rec))
    assert "[task.consolidate]" in out
    assert "agent=@orchestrator" in out
    assert "hi" in out
    assert '"a":1' in out


def test_prettify_events_line_graceful_on_non_json():
    assert L._prettify_events_line("not-json\n") == "not-json"


def test_cli_missing_log_file_returns_1(tmp_paths, capsys):
    tmp_paths.logs.mkdir(exist_ok=True)
    rc = L.main(["gateway"])
    assert rc == 1
    err = capsys.readouterr().err
    assert "no log at" in err


def test_cli_tails_existing_log(tmp_paths, capsys):
    tmp_paths.logs.mkdir(exist_ok=True)
    log = tmp_paths.logs / "schedule.log"
    log.write_text("\n".join(f"line-{i}" for i in range(20)) + "\n")
    rc = L.main(["schedule", "--lines", "5"])
    assert rc == 0
    out = capsys.readouterr().out
    lines = [l for l in out.splitlines() if l.strip()]
    assert len(lines) == 5
    assert "line-19" in lines[-1]


def test_cli_tails_events_and_prettifies(tmp_paths, capsys):
    tmp_paths.events.mkdir(parents=True, exist_ok=True)
    log = tmp_paths.events_log
    payload = json.dumps({"type": "x.y", "agent": "@a", "message": "hello"})
    log.write_text(payload + "\n")
    rc = L.main(["events"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "[x.y]" in out
    assert "agent=@a" in out
    assert "hello" in out


def test_format_age_buckets():
    # _format_age is coarse-by-design: seconds → minutes → hours → days.
    # The header is a freshness gauge ("is this stale?"), not a clock.
    assert L._format_age(0) == "0s ago"
    assert L._format_age(45) == "45s ago"
    assert L._format_age(60) == "1m ago"
    assert L._format_age(125) == "2m ago"
    assert L._format_age(3600) == "1h ago"
    assert L._format_age(86400) == "1d ago"
    assert L._format_age(4 * 86400 + 10) == "4d ago"


def test_header_line_renders_mtime_and_age(tmp_path):
    log = tmp_path / "heartbeat.log"
    log.write_text("line\n")
    mtime = log.stat().st_mtime
    line = L._header_line(log, now=mtime + 5)
    assert line.startswith("# heartbeat.log — last write ")
    assert "Z (" in line
    assert "5s ago)" in line


def test_header_line_when_stat_fails(tmp_path):
    line = L._header_line(tmp_path / "missing.log")
    assert line == "# missing.log — (stat failed)"


def test_index_renders_age_and_path_for_existing_log(tmp_paths):
    tmp_paths.logs.mkdir(exist_ok=True)
    log = tmp_paths.logs / "gateway.log"
    log.write_text("x\n")
    mtime = log.stat().st_mtime
    rows = L._index(tmp_paths, now=mtime + 5)
    gateway_row = next(r for r in rows if r.lstrip().startswith("gateway "))
    assert "5s ago" in gateway_row
    assert str(log) in gateway_row


def test_index_marks_missing_logs(tmp_paths):
    # posthook-suppressions.log only exists when posthook.py has fired
    # a suppression. Index should show "(no log yet)" rather than a
    # confusing "0s ago" or a stat error.
    tmp_paths.logs.mkdir(exist_ok=True)
    rows = L._index(tmp_paths)
    posthook_row = next(r for r in rows if r.lstrip().startswith("posthook "))
    assert "--" in posthook_row
    assert "(no log yet)" in posthook_row


def test_cli_no_args_prints_index(tmp_paths, capsys):
    tmp_paths.logs.mkdir(exist_ok=True)
    (tmp_paths.logs / "schedule.log").write_text("ok\n")
    rc = L.main([])
    assert rc == 0
    out = capsys.readouterr().out
    assert "schedule" in out
    assert "gateway" in out
    assert "events" in out
    # Footer hint is part of the index UX, not a log line.
    assert "metasphere logs <service>" in out


def test_cli_prints_header_to_stderr(tmp_paths, capsys):
    # Header is informational, not part of the log content — it goes to
    # stderr so pipelines (`metasphere logs schedule | grep foo`) stay
    # clean.
    tmp_paths.logs.mkdir(exist_ok=True)
    log = tmp_paths.logs / "schedule.log"
    log.write_text("line-1\nline-2\n")
    rc = L.main(["schedule"])
    assert rc == 0
    captured = capsys.readouterr()
    assert "# schedule.log — last write" in captured.err
    assert "# schedule.log" not in captured.out
    assert "line-1" in captured.out
    assert "line-2" in captured.out
