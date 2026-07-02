"""``metasphere trace`` — command-trace capture + query CLI.

Front-end to ``metasphere.trace``: capture a wrapped command's argv +
exit status, list previously captured traces, search by free-text, and
prune old entries. Traces are how the harness builds up a history of
operator-invoked shell commands that downstream agents (and the
posthook flow) can replay or grep against. This module is the only
write path for the trace store.
"""

from __future__ import annotations

DESCRIPTION = "Capture, list, search, and prune command traces."

USAGE = """\
Usage: metasphere trace <command> [args...]

Commands:
  capture <argv...>            Run <argv...>, record exit code,
                               stdout/stderr, and command for later
                               inspection.
  list [--errors] [--limit N]  List recent traces.
  search <pattern>             Grep across captured traces.
  prune <days>                 Delete traces older than <days>.

Trace records live under `~/.metasphere/state/traces/`.
"""


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
        sys.stdout.write(USAGE)
        return 0
    if not args:
        sys.stderr.write(USAGE)
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
                i += 1
                continue
            if a in ("--limit", "-n") and i + 1 < len(rest):
                try:
                    limit = int(rest[i + 1])
                except ValueError:
                    print(
                        f"trace list: --limit expects an integer, got "
                        f"{rest[i + 1]!r}",
                        file=sys.stderr,
                    )
                    return 2
                i += 2
                continue
            kind = "flag" if a.startswith("-") else "argument"
            print(
                f"trace list: unexpected {kind}: {a}\n"
                f"Usage: trace list [--errors] [--limit N]",
                file=sys.stderr,
            )
            return 2
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
        # Reject flag-shaped tokens so ``trace prune --help`` doesn't
        # detonate inside ``int(...)`` and dump a traceback. Same class
        # as the schedule.enable / spawn-name flag-leak guards.
        if rest[0] in ("--help", "-h"):
            sys.stdout.write(USAGE)
            return 0
        if rest[0].startswith("-") and not rest[0].lstrip("-").isdigit():
            print(
                f"trace prune: {rest[0]!r} looks like a CLI flag, not a "
                f"day count",
                file=sys.stderr,
            )
            return 2
        try:
            days = int(rest[0])
        except ValueError:
            print(
                f"trace prune: <days> expects an integer, got {rest[0]!r}",
                file=sys.stderr,
            )
            return 2
        # Reject negative day counts: ``prune_traces(-N)`` would set the
        # cutoff to today+N, which classifies *every* trace dir as
        # "older than cutoff" and silently wipes the whole tree.
        if days < 0:
            print(
                f"trace prune: <days> must be non-negative, got {days}",
                file=sys.stderr,
            )
            return 2
        n = prune_traces(days, paths=paths)
        print(f"removed {n} day-dirs")
        return 0

    print(f"unknown subcommand: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
