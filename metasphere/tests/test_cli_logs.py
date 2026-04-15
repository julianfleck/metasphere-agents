"""Tests for ``metasphere logs``."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from metasphere.cli import logs as L


def test_service_path_resolves_gateway_to_logs_dir(tmp_paths):
    p = L._service_path("gateway", tmp_paths)
    assert p == tmp_paths.logs / "gateway.log"


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
