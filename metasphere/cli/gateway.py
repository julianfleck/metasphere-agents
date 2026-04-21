"""``gateway`` CLI entry point.

Subcommands::

    python -m metasphere.cli.gateway daemon [N]    # run gateway daemon
    python -m metasphere.cli.gateway inject "msg"  # inject directly into session
    python -m metasphere.cli.gateway ensure        # start session if needed
    python -m metasphere.cli.gateway status        # session status
    python -m metasphere.cli.gateway restart       # restart claude in session
"""

from __future__ import annotations

import argparse
import sys
from typing import List, Optional

from metasphere.gateway import (
    SESSION_NAME,
    ensure_session,
    render_status as render_monitoring_status,
    restart_session,
    run_daemon,
    session_health,
    start_session,
)
from metasphere.paths import resolve
from metasphere.telegram.inject import submit_to_tmux


def cmd_daemon(args: argparse.Namespace) -> int:
    paths = resolve()
    run_daemon(paths, poll_interval=float(args.interval))
    return 0


def cmd_inject(args: argparse.Namespace) -> int:
    paths = resolve()
    ensure_session(paths)
    ok = submit_to_tmux("@cli", args.text, session=SESSION_NAME)
    return 0 if ok else 1


def cmd_ensure(args: argparse.Namespace) -> int:
    paths = resolve()
    ensure_session(paths)
    alive, idle = session_health(paths)
    print(f"session={SESSION_NAME} alive={alive} idle={idle}s")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    paths = resolve()
    alive, idle = session_health(paths)
    print(f"session={SESSION_NAME} alive={alive} idle={idle}s")
    print(render_monitoring_status(paths))
    return 0 if alive else 1


def cmd_restart(args: argparse.Namespace) -> int:
    paths = resolve()
    alive, _ = session_health(paths)
    if not alive:
        start_session(paths)
        print(f"session={SESSION_NAME} started")
        return 0
    restart_session("CLI restart", paths)
    print(f"session={SESSION_NAME} claude restarted")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gateway", description="metasphere gateway CLI")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_daemon = sub.add_parser("daemon", help="run gateway daemon (poll + watchdog)")
    p_daemon.add_argument("interval", nargs="?", default="3", help="poll interval seconds")
    p_daemon.set_defaults(func=cmd_daemon)

    p_inject = sub.add_parser("inject", help="inject text into orchestrator session")
    p_inject.add_argument("text")
    p_inject.set_defaults(func=cmd_inject)

    p_ensure = sub.add_parser("ensure", help="start session if not alive")
    p_ensure.set_defaults(func=cmd_ensure)

    p_status = sub.add_parser("status", help="session status")
    p_status.set_defaults(func=cmd_status)

    p_restart = sub.add_parser("restart", help="restart claude inside session")
    p_restart.set_defaults(func=cmd_restart)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
