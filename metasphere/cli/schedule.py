"""CLI shim mirroring ``scripts/metasphere-schedule``.

Usage::

    python -m metasphere.cli.schedule                  # default = list
    python -m metasphere.cli.schedule list
    python -m metasphere.cli.schedule run              # one tick
    python -m metasphere.cli.schedule daemon [N]       # loop, default 60s
    python -m metasphere.cli.schedule enable <id>
    python -m metasphere.cli.schedule disable <id>
    python -m metasphere.cli.schedule wire-exit-self [--dry-run]
"""

from __future__ import annotations

import datetime as _dt
import sys
import time

from metasphere import paths as _paths
from metasphere import schedule as _sched


def _fmt_ts(ts: int) -> str:
    if not ts:
        return "never"
    return _dt.datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")


def _cmd_list(project_filter: str | None = None) -> int:
    paths = _paths.resolve()
    jobs = _sched.list_jobs(paths)
    if project_filter:
        jobs = [j for j in jobs if _job_matches_project(j, project_filter)]
    if not jobs:
        msg = f"(no scheduled jobs for {project_filter})" if project_filter else "(no scheduled jobs)"
        print(msg)
        return 0
    from metasphere.format import format_schedule_table
    ordered = sorted(jobs, key=lambda x: (not x.enabled, x.name))
    header = f"Scheduled Jobs ({len(jobs)})"
    if project_filter:
        header = f"Scheduled Jobs [{project_filter}] ({len(jobs)})"
    print(header)
    print()
    print(format_schedule_table(ordered))
    return 0


def _job_matches_project(job, project_name: str) -> bool:
    """Return True if a job's name or target suggests it belongs to a
    project. Heuristic: the job name contains the project name as a
    prefix (e.g. ``cam:weekly-review``)."""
    name = getattr(job, "name", "") or ""
    return name.startswith(f"{project_name}:") or name == project_name


def _cmd_run() -> int:
    paths = _paths.resolve()
    results = _sched.run_due_jobs(paths)
    if not results:
        return 0
    for r in results:
        status = "ok" if r.dispatched else f"FAIL ({r.error})"
        print(f"[fire] {r.target_agent}: {r.name} -- {status}")
    return 0


def _cmd_daemon(argv: list[str]) -> int:
    if argv:
        try:
            interval = int(argv[0])
        except ValueError:
            print(f"usage: schedule daemon [interval-seconds]; got: {argv[0]!r}", file=sys.stderr)
            return 2
    else:
        interval = 60
    print(f"Schedule daemon started (check interval: {interval}s)")
    while True:
        try:
            _cmd_run()
        except Exception as e:
            print(f"[daemon] error: {e}", file=sys.stderr)
        time.sleep(interval)


def _cmd_set_enabled(job_id: str, enabled: bool) -> int:
    if not job_id:
        print("usage: schedule {enable|disable} <job-id>", file=sys.stderr)
        return 2
    paths = _paths.resolve()
    if not _sched.set_enabled(job_id, enabled, paths):
        print(f"job not found: {job_id}", file=sys.stderr)
        return 1
    print(f"{'enabled' if enabled else 'disabled'}: {job_id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("--help", "-h"):
        print(__doc__ or "")
        return 0
    cmd = argv[0] if argv else "list"
    rest = argv[1:]

    if cmd in ("", "list", "ls"):
        project_arg = rest[0] if rest and not rest[0].startswith("-") else None
        return _cmd_list(project_filter=project_arg)
    if cmd in ("run", "check"):
        return _cmd_run()
    if cmd == "daemon":
        return _cmd_daemon(rest)
    if cmd == "enable":
        return _cmd_set_enabled(rest[0] if rest else "", True)
    if cmd == "disable":
        return _cmd_set_enabled(rest[0] if rest else "", False)
    if cmd in ("wire-exit-self", "wire_exit_self"):
        from metasphere.cli.wire_exit_self import main as _wire_main
        return _wire_main(rest)

    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
