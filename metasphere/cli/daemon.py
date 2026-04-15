"""``metasphere daemon start|stop|restart|status`` — systemd wrapper.

Thin wrapper over ``systemctl --user`` for the three services that
together make up the live harness:

- ``metasphere-gateway`` (Telegram poller + orchestrator REPL supervisor)
- ``metasphere-heartbeat`` (periodic agent wake ticker)
- ``metasphere-schedule`` (cron-fire scheduler)

Users shouldn't have to know the unit names or remember to restart all
three after a code pull. ``metasphere daemon restart`` does the right
thing; no systemd knowledge required. For fine-grained control, pass
a service name: ``metasphere daemon restart gateway``.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from typing import Callable, List, Optional


#: The three services ``metasphere daemon`` manages. Keep order
#: predictable (matches the dependency order at boot — gateway owns
#: the orchestrator session, heartbeat pokes it, schedule triggers
#: timed work).
SERVICES = ("gateway", "heartbeat", "schedule")

ACTIONS = ("start", "stop", "restart", "status")


def _service_unit(short: str) -> str:
    return f"metasphere-{short}.service"


def _run(argv: List[str], *, runner: Optional[Callable] = None) -> "subprocess.CompletedProcess":
    runner = runner or subprocess.run
    return runner(
        argv,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )


def _systemctl(action: str, service: str, *,
               runner: Optional[Callable] = None) -> tuple[int, str, str]:
    cp = _run(
        ["systemctl", "--user", action, _service_unit(service)],
        runner=runner,
    )
    return cp.returncode, (cp.stdout or ""), (cp.stderr or "")


def _format_status_line(service: str, rc: int, stdout: str, stderr: str) -> str:
    """Collapse the verbose ``systemctl status`` output to one line.

    ``systemctl is-active`` would be cleaner but doesn't expose the
    sub-state (e.g. "active (running)" vs "activating"). We cherry-pick
    the ``Active:`` line from ``status`` output and render only that.
    """
    if rc != 0 and not stdout:
        # Typical ``Unit ... could not be found`` or "inactive" → rc!=0
        # but stderr has the message.
        msg = (stderr.strip().splitlines() or [""])[0]
        return f"{service:10s}  {msg}"
    active_line = ""
    for raw in stdout.splitlines():
        stripped = raw.strip()
        if stripped.startswith("Active:"):
            active_line = stripped[len("Active:"):].strip()
            break
    return f"{service:10s}  {active_line or 'unknown'}"


def cmd_status(args: argparse.Namespace,
               *, runner: Optional[Callable] = None) -> int:
    targets = [args.service] if args.service else list(SERVICES)
    worst_rc = 0
    for svc in targets:
        rc, out, err = _systemctl("status", svc, runner=runner)
        print(_format_status_line(svc, rc, out, err))
        # systemctl status returns 3 for "inactive"; that's not a CLI
        # failure from our perspective, just a reportable state.
        if rc not in (0, 3):
            worst_rc = rc
    return worst_rc


def cmd_lifecycle(args: argparse.Namespace,
                   *, runner: Optional[Callable] = None) -> int:
    targets = [args.service] if args.service else list(SERVICES)
    worst_rc = 0
    for svc in targets:
        rc, out, err = _systemctl(args.action, svc, runner=runner)
        if rc == 0:
            print(f"{svc:10s}  {args.action} ok")
        else:
            msg = (err.strip().splitlines() or out.strip().splitlines() or [""])[0]
            print(f"{svc:10s}  {args.action} failed: {msg}", file=sys.stderr)
            worst_rc = rc
    return worst_rc


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="metasphere daemon",
        description="Control the three metasphere systemd services "
        f"({', '.join(SERVICES)}).",
    )
    p.add_argument("action", choices=ACTIONS,
                   help="Action to perform on the targeted service(s).")
    p.add_argument(
        "service", nargs="?", default=None, choices=SERVICES,
        help=f"Which service (default: all: {', '.join(SERVICES)}).",
    )
    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.action == "status":
        return cmd_status(args)
    return cmd_lifecycle(args)


if __name__ == "__main__":
    raise SystemExit(main())
