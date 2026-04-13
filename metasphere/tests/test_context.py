"""Tests for metasphere.context — per-turn context assembly + drift hash."""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import subprocess
from pathlib import Path

import pytest

from metasphere import context as ctx
from metasphere import messages as _msgs
from metasphere import tasks as _tasks
from metasphere.paths import Paths


# ---------------------------------------------------------------------------
# harness_hash
# ---------------------------------------------------------------------------


def _write(p: Path, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


def test_harness_hash_matches_bash_recipe(tmp_paths: Paths):
    # Populate the three harness files with distinct contents.
    _write(tmp_paths.project_root / "CLAUDE.md", "claude\n")
    _write(tmp_paths.project_root / ".claude" / "settings.json", "{settings}\n")
    _write(tmp_paths.project_root / ".claude" / "settings.local.json", "{local}\n")

    py_hash = ctx.harness_hash(tmp_paths)

    # Reproduce the hash recipe: sort filenames, cat in order,
    # sha256sum the concatenated bytes.
    files = sorted(
        str(tmp_paths.project_root / rel)
        for rel in (
            "CLAUDE.md",
            ".claude/settings.json",
            ".claude/settings.local.json",
        )
    )
    h = hashlib.sha256()
    for f in files:
        h.update(Path(f).read_bytes())
    assert py_hash == h.hexdigest()


def test_harness_hash_empty_when_no_files(tmp_paths: Paths):
    assert ctx.harness_hash(tmp_paths) == ""


# ---------------------------------------------------------------------------
# truncate_section
# ---------------------------------------------------------------------------


def test_truncate_section_caps_long_text():
    long = "x" * 5000
    out = ctx.truncate_section(long, budget=100)
    # The cut keeps ≤ budget bytes plus the truncation marker.
    assert len(out.encode("utf-8")) < 5000
    assert "truncated" in out


def test_truncate_section_passthrough_short_text():
    assert ctx.truncate_section("hello", budget=2048) == "hello"


# ---------------------------------------------------------------------------
# Drift warning
# ---------------------------------------------------------------------------


def test_drift_warning_emitted_when_baseline_differs(tmp_paths: Paths):
    _write(tmp_paths.project_root / "CLAUDE.md", "claude v1\n")
    (tmp_paths.state).mkdir(parents=True, exist_ok=True)
    (tmp_paths.state / "harness_hash_baseline").write_text("deadbeef\n")

    out = ctx.build_context(tmp_paths)
    assert "Harness drift detected" in out


def test_drift_warning_silent_when_baseline_matches(tmp_paths: Paths):
    _write(tmp_paths.project_root / "CLAUDE.md", "claude v1\n")
    live = ctx.harness_hash(tmp_paths)
    (tmp_paths.state).mkdir(parents=True, exist_ok=True)
    (tmp_paths.state / "harness_hash_baseline").write_text(live + "\n")

    out = ctx.build_context(tmp_paths)
    assert "Harness drift detected" not in out


# ---------------------------------------------------------------------------
# build_context: section order + empty-state robustness
# ---------------------------------------------------------------------------


def test_build_context_emits_all_sections_in_order(tmp_paths: Paths):
    # Seed each data source so each section produces an identifiable header.
    # 1. Status
    agent_dir = tmp_paths.agent_dir("@orchestrator")
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "status").write_text("working: porting context.py\n")

    # 2. Drift (force a warning)
    _write(tmp_paths.project_root / "CLAUDE.md", "claude\n")
    tmp_paths.state.mkdir(parents=True, exist_ok=True)
    (tmp_paths.state / "harness_hash_baseline").write_text("deadbeef\n")

    # 3. Telegram
    today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    tg_file = tmp_paths.telegram_stream / f"{today}.jsonl"
    tg_file.parent.mkdir(parents=True, exist_ok=True)
    tg_file.write_text(
        json.dumps(
            {"from": {"username": "testuser"}, "text": "hello", "date": 1775539062}
        )
        + "\n"
    )

    # 4. Messages
    _msgs.send_message(
        "@.", "!info", "test message body", "@user", paths=tmp_paths, wake=False
    )

    # 5. Tasks
    _tasks.create_task(
        "ship the port", _tasks.PRIORITY_DEFAULT, tmp_paths.scope, tmp_paths.project_root
    )

    # 6. Events
    from metasphere.events import log_event

    log_event("test.event", "hello world", paths=tmp_paths)

    out = ctx.build_context(tmp_paths)

    # Each section header must appear, in order.
    headers_in_order = [
        "# Metasphere Delta",        # status
        "## ⚠ Harness drift",        # drift
        "## Telegram (recent",       # telegram
        "## Messages",               # messages
        "## Tasks",                  # tasks
        "## Recent Events",          # events
        "## Memory Context (FTS)",   # memory
    ]
    last = -1
    for h in headers_in_order:
        idx = out.find(h)
        assert idx != -1, f"missing section header: {h}\n---\n{out}"
        assert idx > last, f"section out of order: {h}"
        last = idx


def test_build_context_empty_state_does_not_crash(tmp_paths: Paths):
    out = ctx.build_context(tmp_paths)
    # Status header is always present even with no agent dir.
    assert "Metasphere Delta" in out
    # Empty inbox / tasks / events render the "no ..." sentinels rather
    # than blowing up.
    assert "## Messages" in out
    assert "## Tasks" in out
    assert "## Recent Events" in out
    assert "## Memory Context (FTS)" in out
