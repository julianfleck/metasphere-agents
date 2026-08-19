"""``metasphere daemon`` — service-manager wrapper for harness daemons.

Front-end for the three long-running metasphere services (gateway,
heartbeat, schedule). Linux uses ``systemctl --user``; macOS uses
``launchctl`` in the current GUI domain. This module owns both naming
conventions and returns the underlying service-manager exit code.
"""

from __future__ import annotations

DESCRIPTION = "Start/stop/restart/status the three metasphere services."

USAGE = """\
Usage: metasphere daemon <action> [<service>]

Actions:
  start    Start the targeted service(s).
  stop     Stop the targeted service(s).
  restart  Restart the targeted service(s).
  status   Print one-line Active state per service.

Services (default: all three):
  gateway     Telegram poller + orchestrator REPL supervisor.
  heartbeat   Periodic agent-wake ticker.
  schedule    Cron-fire scheduler.

With no <service>, every action applies to all three. The order is
boot-dependency order (gateway, heartbeat, schedule).
"""


import argparse
import os
import subprocess
import sys
from pathlib import Path
from typing import Callable, List, Optional


#: The three services ``metasphere daemon`` manages. Keep order
#: predictable (matches the dependency order at boot — gateway owns
#: the orchestrator session, heartbeat pokes it, schedule triggers
#: timed work).
SERVICES = ("gateway", "heartbeat", "schedule")

ACTIONS = ("start", "stop", "restart", "status")


def _service_unit(short: str) -> str:
    return f"metasphere-{short}.service"


def _is_macos() -> bool:
    return sys.platform == "darwin"


def _launchd_label(short: str) -> str:
    return f"com.metasphere.{short}"


def _launchd_target(short: str) -> str:
    return f"gui/{os.getuid()}/{_launchd_label(short)}"


def _launchd_plist(short: str) -> str:
    return str(Path.home() / "Library" / "LaunchAgents" / f"{_launchd_label(short)}.plist")


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


def _launchctl(action: str, service: str, *,
               runner: Optional[Callable] = None) -> tuple[int, str, str]:
    target = _launchd_target(service)
    if action == "status":
        argv = ["launchctl", "print", target]
    elif action == "stop":
        argv = ["launchctl", "bootout", target]
    elif action == "restart":
        # ``kickstart -k`` restarts the process but retains launchd's old
        # in-memory plist. Re-bootstrap so installer-rendered environment
        # and command changes actually become active.
        probe = _run(["launchctl", "print", target], runner=runner)
        if probe.returncode == 0:
            stopped = _run(["launchctl", "bootout", target], runner=runner)
            if stopped.returncode != 0:
                return (
                    stopped.returncode,
                    (stopped.stdout or ""),
                    (stopped.stderr or ""),
                )
        argv = [
            "launchctl",
            "bootstrap",
            f"gui/{os.getuid()}",
            _launchd_plist(service),
        ]
    elif action == "start":
        probe = _run(["launchctl", "print", target], runner=runner)
        if probe.returncode == 0:
            argv = ["launchctl", "kickstart", target]
        else:
            argv = ["launchctl", "bootstrap", f"gui/{os.getuid()}", _launchd_plist(service)]
    else:
        raise ValueError(f"unsupported launchctl action: {action}")
    cp = _run(argv, runner=runner)
    return cp.returncode, (cp.stdout or ""), (cp.stderr or "")


def _service_call(action: str, service: str, *,
                  runner: Optional[Callable] = None) -> tuple[int, str, str]:
    if _is_macos():
        return _launchctl(action, service, runner=runner)
    return _systemctl(action, service, runner=runner)


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


def _format_launchd_status_line(service: str, rc: int, stdout: str, stderr: str) -> str:
    if rc != 0:
        return f"{service:10s}  not loaded"
    state = "unknown"
    pid = ""
    for raw in stdout.splitlines():
        stripped = raw.strip()
        if stripped.startswith("state ="):
            state = stripped.partition("=")[2].strip()
        elif stripped.startswith("pid ="):
            pid = stripped.partition("=")[2].strip()
    suffix = f" (pid {pid})" if pid else ""
    return f"{service:10s}  {state}{suffix}"


def cmd_status(args: argparse.Namespace,
               *, runner: Optional[Callable] = None) -> int:
    targets = [args.service] if args.service else list(SERVICES)
    worst_rc = 0
    for svc in targets:
        rc, out, err = _service_call("status", svc, runner=runner)
        if _is_macos():
            print(_format_launchd_status_line(svc, rc, out, err))
            continue
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
        rc, out, err = _service_call(args.action, svc, runner=runner)
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
        description="Control the three metasphere services "
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
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list and args_list[0] in ("--help", "-h"):
        sys.stdout.write(USAGE)
        return 0
    parser = build_parser()
    args = parser.parse_args(args_list)
    if args.action == "status":
        return cmd_status(args)
    return cmd_lifecycle(args)


if __name__ == "__main__":
    raise SystemExit(main())
