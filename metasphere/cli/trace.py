"""CLI: ``python -m metasphere.cli.trace``.

Subcommands::

    trace capture <argv...>
    trace list [--errors] [--limit N]
    trace search <pattern>
    trace prune <days>
"""

from __future__ import annotations

import json
import sys

from metasphere.paths import resolve
from metasphere.trace import (
    capture_trace,
    list_traces,
    prune_traces,
    search_traces,
)


def _print_trace_row(t) -> None:
    mark = "x" if t.error_detected else " "
    print(f"[{mark}] {t.id} {t.timestamp} exit={t.exit_code} {t.command[:60]}")


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] in ("--help", "-h"):
        print(__doc__ or "")
        return 0
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    cmd, *rest = args
    paths = resolve()

    if cmd in ("capture", "run", "exec"):
        if not rest:
            print("usage: trace capture <command...>", file=sys.stderr)
            return 2
        # If single string with spaces, run via shell; else argv
        if len(rest) == 1:
            t = capture_trace(rest[0], paths=paths)
        else:
            t = capture_trace(rest, paths=paths)
        print(json.dumps(t.to_dict(), indent=2))
        return t.exit_code

    if cmd in ("list", "ls"):
        errors_only = False
        limit = 20
        i = 0
        while i < len(rest):
            a = rest[i]
            if a in ("--errors", "-e"):
                errors_only = True
            elif a in ("--limit", "-n") and i + 1 < len(rest):
                limit = int(rest[i + 1])
                i += 1
            i += 1
        for t in list_traces(limit=limit, errors_only=errors_only, paths=paths):
            _print_trace_row(t)
        return 0

    if cmd in ("search", "find"):
        if not rest:
            print("usage: trace search <pattern>", file=sys.stderr)
            return 2
        for t in search_traces(" ".join(rest), paths=paths):
            _print_trace_row(t)
        return 0

    if cmd == "prune":
        if not rest:
            print("usage: trace prune <days>", file=sys.stderr)
            return 2
        n = prune_traces(int(rest[0]), paths=paths)
        print(f"removed {n} day-dirs")
        return 0

    print(f"unknown subcommand: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
