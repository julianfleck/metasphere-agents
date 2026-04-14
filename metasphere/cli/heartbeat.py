"""CLI for the heartbeat daemon.

Usage::

    python -m metasphere.cli.heartbeat              # one-shot
    python -m metasphere.cli.heartbeat daemon       # default 30s
    python -m metasphere.cli.heartbeat daemon 300   # custom interval
    python -m metasphere.cli.heartbeat --invoke-agent

Runs as a systemd unit with ``HEARTBEAT_INVOKE_AGENT=true`` to
trigger agent context submission on each tick.
"""

from __future__ import annotations

import os
import sys

from metasphere.heartbeat import heartbeat_daemon, heartbeat_once
from metasphere.paths import resolve


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] in ("--help", "-h"):
        print(__doc__ or "")
        return 0

    invoke_agent = os.environ.get("HEARTBEAT_INVOKE_AGENT", "").lower() == "true"
    if "--invoke-agent" in args:
        invoke_agent = True
        args = [a for a in args if a != "--invoke-agent"]

    paths = resolve()

    if not args or args[0] in ("once", "check"):
        heartbeat_once(paths, invoke_agent=invoke_agent)
        return 0

    if args[0] == "daemon":
        interval = 30
        if len(args) > 1:
            try:
                interval = int(args[1])
            except ValueError:
                print(f"invalid interval: {args[1]}", file=sys.stderr)
                return 2
        heartbeat_daemon(
            paths,
            interval_seconds=interval,
            invoke_agent=invoke_agent,
        )
        return 0

    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
