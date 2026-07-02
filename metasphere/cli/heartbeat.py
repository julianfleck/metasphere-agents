"""``metasphere heartbeat`` — per-tick heartbeat entrypoint.

Two modes back the same code path: a one-shot invocation (cron / hook
caller wants a single tick) and a long-running daemon loop run under
systemd. The tick body lives in ``metasphere.heartbeat`` and injects a
short status nudge into each alive agent tmux session so idle REPLs
get a wake signal at a predictable cadence. Cadence + which agents are
in scope are decided by the heartbeat module, not by this shim.
"""

from __future__ import annotations

DESCRIPTION = "Per-tick heartbeat: one-shot or long-running daemon."

USAGE = """\
Usage: metasphere heartbeat [<command>] [args...]

Commands:
  (no args)                    One-shot tick (alias for `once`).
  once                         One-shot tick.
  check                        One-shot tick.
  daemon [<interval-seconds>]  Run forever; default interval 300s
                               (5 minutes, matches the systemd unit).

Options:
  --invoke-agent               Inject the per-turn context block into
                               every active agent on each tick. Equivalent
                               to setting HEARTBEAT_INVOKE_AGENT=true in
                               the environment.

Runs as the `metasphere-heartbeat.service` systemd user unit with
HEARTBEAT_INVOKE_AGENT=true so agent REPLs receive the ~5-minute
context refresh.
"""


import os
import sys

from metasphere.heartbeat import heartbeat_daemon, heartbeat_once
from metasphere.paths import resolve


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] in ("--help", "-h"):
        sys.stdout.write(USAGE)
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
        interval = 300
        if len(args) > 1:
            raw = args[1]
            if raw in ("--help", "-h"):
                sys.stdout.write(USAGE)
                return 0
            # Reject flag-shaped intervals so ``heartbeat daemon --bogus``
            # surfaces a clean rc=2 instead of the bare ``invalid interval:
            # '--bogus'`` line. Same class as schedule.daemon / consolidate
            # run / trace prune hardening.
            if raw.startswith("-") and not raw.lstrip("-").isdigit():
                print(
                    f"heartbeat daemon: {raw!r} looks like a CLI flag, not an interval",
                    file=sys.stderr,
                )
                return 2
            try:
                interval = int(raw)
            except ValueError:
                print(
                    f"heartbeat daemon: interval expects an integer, got {raw!r}",
                    file=sys.stderr,
                )
                return 2
            # A negative interval would crash ``time.sleep`` inside the
            # daemon loop. Reject up-front.
            if interval < 0:
                print(
                    f"heartbeat daemon: interval must be non-negative, got {interval}",
                    file=sys.stderr,
                )
                return 2
        heartbeat_daemon(
            paths,
            interval_seconds=interval,
            invoke_agent=invoke_agent,
        )
        return 0

    sys.stderr.write(USAGE)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
