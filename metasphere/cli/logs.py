"""``metasphere logs`` — tail metasphere service logs.

Single-pane log tailer that resolves service-name aliases (``gateway``,
``heartbeat``, ``schedule``, ``reaper``, ``posthook``, ``update``,
``events``) onto the underlying log file paths via
``metasphere.paths``. Supports ``--lines N`` and ``-f`` follow mode;
multi-file follow is interleaved by file mtime. Read-only — never
rotates or truncates the files it reads.
"""

from __future__ import annotations

DESCRIPTION = "Tail gateway / heartbeat / schedule / reaper / posthook / update / events logs."

USAGE = """\
Usage: metasphere logs [<service>] [--lines N] [-f]

Services:
  gateway     ~/.metasphere/logs/gateway.log
  heartbeat   ~/.metasphere/logs/heartbeat.log
  schedule    ~/.metasphere/logs/schedule.log
  reaper      ~/.metasphere/logs/reaper.log
  posthook    ~/.metasphere/logs/posthook-suppressions.log
  update      ~/.metasphere/logs/auto-update.log
  events      Today's ~/.metasphere/events/events-YYYY-MM-DD.jsonl
              (pretty-printed JSON, one record per line).

Options:
  --lines N, -n N   Initial tail size (default 50).
  -f, --follow      Follow appended output (like `tail -f`).

Without -f, the command prints the last N lines and exits.
Without a service, prints an index of all logs with their last-write age.
"""


import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from metasphere.paths import resolve


SERVICES = ("gateway", "heartbeat", "schedule", "reaper", "posthook", "update", "events")


def _service_path(which: str, paths) -> Path:
    if which == "events":
        return paths.events_log
    if which == "posthook":
        return paths.logs / "posthook-suppressions.log"
    if which == "update":
        return paths.logs / "auto-update.log"
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


def _format_age(seconds: float) -> str:
    """Render an elapsed-time delta as `Ns`/`Nm`/`Nh`/`Nd ago`.

    Coarse on purpose — the header is a freshness gauge, not a clock.
    """
    s = max(0, int(seconds))
    if s < 60:
        return f"{s}s ago"
    if s < 3600:
        return f"{s // 60}m ago"
    if s < 86400:
        return f"{s // 3600}h ago"
    return f"{s // 86400}d ago"


def _header_line(path: Path, *, now: Optional[float] = None) -> str:
    """Return a one-line `# <name> — last write <iso> (<age>)` header.

    Surfaces file mtime so operators can tell whether the tail is fresh
    or frozen. Many log lines (e.g., heartbeat's `[tmux.submit] defer:`)
    have no embedded timestamp, so a 50-line tail can look identical
    whether the daemon is silent today or stopped writing 4 days ago.
    """
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return f"# {path.name} — (stat failed)"
    ts = datetime.fromtimestamp(mtime, tz=timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ")
    age = _format_age((now if now is not None else time.time()) - mtime)
    return f"# {path.name} — last write {ts} ({age})"


def _index(paths, *, now: Optional[float] = None) -> List[str]:
    """One row per service: ``<name>  <age>  <path-or-marker>``.

    Mirrors the freshness header but for every known log at once, so an
    operator running ``metasphere logs`` with no args sees which logs
    exist and which are fresh before drilling into one.
    """
    width = max(len(s) for s in SERVICES)
    rows: List[str] = []
    for svc in SERVICES:
        path = _service_path(svc, paths)
        if path.is_file():
            try:
                mtime = path.stat().st_mtime
            except OSError:
                age = "--"
                disp = "(stat failed)"
            else:
                age = _format_age((now if now is not None else time.time()) - mtime)
                disp = str(path)
        else:
            age = "--"
            disp = "(no log yet)"
        rows.append(f"  {svc:<{width}}  {age:<10}  {disp}")
    return rows


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
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list and args_list[0] in ("--help", "-h"):
        sys.stdout.write(USAGE)
        return 0
    parser = argparse.ArgumentParser(
        prog="metasphere logs",
        description="Tail metasphere service logs. Replaces ``journalctl "
        "--user-unit`` for the common debugging case.",
    )
    parser.add_argument(
        "service", choices=SERVICES, nargs="?",
        help=f"Which log: {', '.join(SERVICES)}. Omit to list all.",
    )
    parser.add_argument("--lines", "-n", type=int, default=50,
                        help="Initial tail size (default 50).")
    parser.add_argument("-f", "--follow", action="store_true",
                        help="Follow appended output (like tail -f).")
    args = parser.parse_args(args_list)

    paths = resolve()

    if args.service is None:
        for row in _index(paths):
            print(row)
        print("\nUse `metasphere logs <service>` to tail one.")
        return 0

    path = _service_path(args.service, paths)
    is_events = args.service == "events"

    if not path.is_file():
        print(f"metasphere logs: no log at {path}", file=sys.stderr)
        return 1

    # Freshness header — skipped under -f, where the live tail makes
    # mtime self-evident and the static header would scroll out anyway.
    if not args.follow:
        print(_header_line(path), file=sys.stderr)
    _render(_tail_lines(path, args.lines), is_events=is_events)
    if args.follow:
        try:
            _follow(path, is_events=is_events)
        except KeyboardInterrupt:
            return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
