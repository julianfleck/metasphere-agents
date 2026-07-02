"""``metasphere events`` — structured event-log tail + retention CLI.

Front-end to ``metasphere.events``: tail the recent event stream and
apply retention to the dated ``events-YYYY-MM-DD.jsonl`` files under
``~/.metasphere/events/``.

The event store has no automatic retention: it is append-only and grows
slowly but unbounded. ``events prune`` is the operator lever for that —
delete old day-files, or (recommended) ``--compress`` them to ``.gz`` for
a reversible ~90% *logical* reclaim that ``events tail`` still reads
transparently. (On a compressing filesystem like ZFS/btrfs the on-disk
files are already compressed, so the physical space freed is much less
than the logical figure — ``delete`` is the stronger reclaim lever there.)
Like ``metasphere trace prune``, nothing runs this on a schedule; it only
acts when invoked.
"""

from __future__ import annotations

DESCRIPTION = "Tail the event stream and prune/compress old event logs."

USAGE = """\
Usage: metasphere events <command> [args...]

Commands:
  tail [--limit N]                 Show the last N events (default 10),
                                   reading across dated day-files and
                                   compressed (.gz) history transparently.
  prune <days> [--compress]        Apply retention to event day-files
        [--dry-run]                dated strictly older than <days> ago.
                                   Default deletes them; --compress gzips
                                   them in place instead (reversible,
                                   lossless; the reported reclaim is
                                   logical — physical savings are smaller
                                   on ZFS/btrfs). --dry-run reports what
                                   would happen without touching disk.

Event logs live under `~/.metasphere/events/`. The current day's log is
always retained (with <days>=0 only strictly-older files qualify), so the
live append target is never disturbed. Nothing runs prune automatically.
"""

import sys

from metasphere.events import prune_events, tail_events
from metasphere.paths import resolve


def _fmt_bytes(n: int) -> str:
    f = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if f < 1024 or unit == "GB":
            return f"{f:.1f}{unit}" if unit != "B" else f"{int(f)}B"
        f /= 1024
    return f"{int(n)}B"


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

    if cmd in ("tail", "ls", "list"):
        limit = 10
        i = 0
        while i < len(rest):
            a = rest[i]
            if a in ("--help", "-h"):
                sys.stdout.write(USAGE)
                return 0
            if a in ("--limit", "-n") and i + 1 < len(rest):
                try:
                    limit = int(rest[i + 1])
                except ValueError:
                    print(
                        f"events tail: --limit expects an integer, got "
                        f"{rest[i + 1]!r}",
                        file=sys.stderr,
                    )
                    return 2
                i += 2
                continue
            kind = "flag" if a.startswith("-") else "argument"
            print(
                f"events tail: unexpected {kind}: {a}\n"
                f"Usage: metasphere events tail [--limit N]",
                file=sys.stderr,
            )
            return 2
        print(tail_events(limit, paths=paths))
        return 0

    if cmd == "prune":
        if not rest:
            print("usage: events prune <days> [--compress] [--dry-run]", file=sys.stderr)
            return 2
        # Guard flag-shaped tokens so ``events prune --help`` (and stray
        # flags before <days>) don't detonate inside ``int(...)``. Same
        # class as the trace.prune / schedule.enable flag-leak guards.
        if rest[0] in ("--help", "-h"):
            sys.stdout.write(USAGE)
            return 0
        compress = False
        dry_run = False
        days_tok: str | None = None
        for a in rest:
            if a == "--compress":
                compress = True
            elif a == "--dry-run":
                dry_run = True
            elif a in ("--help", "-h"):
                sys.stdout.write(USAGE)
                return 0
            elif a.startswith("-") and not a.lstrip("-").isdigit():
                print(
                    f"events prune: unknown flag {a!r}\n"
                    f"Usage: metasphere events prune <days> [--compress] [--dry-run]",
                    file=sys.stderr,
                )
                return 2
            elif days_tok is None:
                days_tok = a
            else:
                print(
                    f"events prune: unexpected argument {a!r}",
                    file=sys.stderr,
                )
                return 2
        if days_tok is None:
            print("usage: events prune <days> [--compress] [--dry-run]", file=sys.stderr)
            return 2
        try:
            days = int(days_tok)
        except ValueError:
            print(
                f"events prune: <days> expects an integer, got {days_tok!r}",
                file=sys.stderr,
            )
            return 2
        # Reject negatives: a negative count pushes the cutoff into the
        # future, classifying *every* file as older-than-cutoff and wiping
        # the whole store. Same footgun guarded in trace.prune.
        if days < 0:
            print(
                f"events prune: <days> must be non-negative, got {days}",
                file=sys.stderr,
            )
            return 2
        result = prune_events(days, compress=compress, dry_run=dry_run, paths=paths)
        prefix = "[dry-run] would " if dry_run else ""
        if compress:
            print(
                f"{prefix}compress {len(result.compressed)} day-file(s)"
                + (
                    f", reclaiming ~{_fmt_bytes(result.bytes_reclaimed)} logical "
                    f"(physical reclaim is smaller on compressing "
                    f"filesystems, e.g. ZFS/btrfs — delete frees more)"
                    if not dry_run
                    else ""
                )
            )
        else:
            print(
                f"{prefix}delete {len(result.deleted)} day-file(s), "
                f"{prefix}free {_fmt_bytes(result.bytes_reclaimed)}"
            )
        return 0

    print(f"unknown subcommand: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
