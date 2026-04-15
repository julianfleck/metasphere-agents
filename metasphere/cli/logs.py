"""``metasphere logs [gateway|heartbeat|schedule|events] [-f] [--lines N]``.

Tail the appropriate log file for daemon debugging:

- ``gateway``    → ``~/.metasphere/logs/gateway.log``
- ``heartbeat``  → ``~/.metasphere/logs/heartbeat.log``
- ``schedule``   → ``~/.metasphere/logs/schedule.log``
- ``events``     → today's ``~/.metasphere/events/events-YYYY-MM-DD.jsonl``
                   (pretty-printed JSON, one record per line)

``--lines N`` controls the initial tail size (default 50). ``-f`` / ``--follow``
streams new content as it arrives (like ``tail -f``). Without ``-f`` the
command prints the last N lines and exits.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path
from typing import List, Optional

from metasphere.paths import resolve


SERVICES = ("gateway", "heartbeat", "schedule", "events")


def _service_path(which: str, paths) -> Path:
    if which == "events":
        return paths.events_log
    return paths.logs / f"{which}.log"


def _prettify_events_line(raw: str) -> str:
    """Events log is JSONL — format each object for human scan."""
    try:
        rec = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return raw.rstrip("\n")
    ts = rec.get("timestamp") or rec.get("ts") or ""
    typ = rec.get("type", "?")
    agent = rec.get("agent", "")
    msg = rec.get("message", "")
    meta = rec.get("meta", {})
    parts = [f"{ts} [{typ}]"]
    if agent:
        parts.append(f"agent={agent}")
    if msg:
        parts.append(msg)
    if meta:
        parts.append(f"meta={json.dumps(meta, separators=(',', ':'))}")
    return " ".join(parts)


def _tail_lines(path: Path, n: int) -> List[str]:
    """Return the last ``n`` lines of ``path`` (or fewer if shorter).

    Simple read-all-and-slice — log files here are typically a few MB,
    not worth an mmap ring buffer. If a file ever grows past that, swap
    for ``collections.deque(f, n)``.
    """
    if not path.is_file():
        return []
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except OSError:
        return []
    return lines[-n:]


def _render(lines: List[str], *, is_events: bool) -> None:
    for ln in lines:
        if is_events:
            print(_prettify_events_line(ln))
        else:
            sys.stdout.write(ln if ln.endswith("\n") else ln + "\n")


def _follow(path: Path, *, is_events: bool,
             sleep_fn=time.sleep, stop_fn=None) -> None:
    """Tail-follow ``path``, polling every 250ms. Handles log rotation
    (inode change) by reopening when the current fd's offset exceeds
    the file size.
    """
    offset = path.stat().st_size if path.is_file() else 0
    while True:
        if stop_fn is not None and stop_fn():
            return
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                f.seek(offset)
                chunk = f.read()
                if chunk:
                    for line in chunk.splitlines(keepends=True):
                        if is_events:
                            print(_prettify_events_line(line))
                        else:
                            sys.stdout.write(line if line.endswith("\n") else line + "\n")
                    sys.stdout.flush()
                offset = f.tell()
            # Detect truncation / rotation: if the file shrank below
            # our offset, reset to start.
            try:
                size = path.stat().st_size
            except OSError:
                size = offset
            if size < offset:
                offset = 0
        except OSError:
            pass
        sleep_fn(0.25)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="metasphere logs",
        description="Tail metasphere service logs. Replaces ``journalctl "
        "--user-unit`` for the common debugging case.",
    )
    parser.add_argument(
        "service", choices=SERVICES,
        help=f"Which log: {', '.join(SERVICES)}.",
    )
    parser.add_argument("--lines", "-n", type=int, default=50,
                        help="Initial tail size (default 50).")
    parser.add_argument("-f", "--follow", action="store_true",
                        help="Follow appended output (like tail -f).")
    args = parser.parse_args(argv)

    paths = resolve()
    path = _service_path(args.service, paths)
    is_events = args.service == "events"

    if not path.is_file():
        print(f"metasphere logs: no log at {path}", file=sys.stderr)
        return 1

    _render(_tail_lines(path, args.lines), is_events=is_events)
    if args.follow:
        try:
            _follow(path, is_events=is_events)
        except KeyboardInterrupt:
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
