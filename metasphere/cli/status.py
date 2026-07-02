"""``metasphere status`` — single-screen system summary.

Pure-Python renderer over ``metasphere.status.summary()`` — listing
live tmux sessions, daemon health, and recent event counts in a
single-screen format intended for ``watch``-style polling and the
top of operator briefings. No state mutation; safe to run from any
hook or hot path.
"""

from __future__ import annotations

import sys


DESCRIPTION = "Print a single-screen system summary."

USAGE = """\
Usage: metasphere status

Print a single-screen summary of the running system: tmux agent
sessions (grouped by liveness), active task count, enabled cron job
count, initialized projects, and orchestrator liveness.

Takes no arguments.
"""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("--help", "-h"):
        sys.stdout.write(USAGE)
        return 0
    if argv:
        # ``metasphere status`` takes no arguments. The pre-hardening
        # path silently dropped any extras (``status --bogus`` → rc=0
        # with the full summary). Reject so typos surface instead of
        # masquerading as success.
        head = argv[0]
        kind = "flag" if head.startswith("-") else "argument"
        sys.stderr.write(
            f"metasphere status: unexpected {kind}: {head}\n"
            f"Usage: metasphere status (takes no arguments)\n"
        )
        return 2
    from metasphere.status import summary
    sys.stdout.write(summary() + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
