"""``metasphere schedule`` — cron-style job scheduler CLI.

Front-end to ``metasphere.schedule``: list configured jobs, run one
ad-hoc, enable/disable, or run the scheduler loop itself as a
daemon. Jobs are stored as on-disk records (not crontab entries) so
the harness owns its own schedule semantics — cadence, scope, and
dispatch target are written through this module, not edited
by hand under ``~/.metasphere/schedule/``.
"""

from __future__ import annotations

DESCRIPTION = "Cron-style job scheduler: list, run, enable, disable, daemon."

USAGE = """\
Usage: metasphere schedule [<command>] [args...]

Commands:
  (no args)                  Default = list.
  list [project]             List all configured cron jobs.
  run                        Fire one tick: dispatch every job whose
                             schedule matches now.
  daemon [<interval>]        Long-running scheduler loop. Default
                             tick interval is 60 seconds.
  enable <id>                Re-enable a disabled job by id.
  disable <id>               Disable a job by id (it stays in the
                             registry but does not fire).
  wire-exit-self [--dry-run] Append the canonical exit-self payload to
                             every job that lacks one.

Job definitions live in `~/.metasphere/cron/<id>.yaml`.
"""


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
    if argv and argv[0] in ("--help", "-h"):
        sys.stdout.write(USAGE)
        return 0
    if argv:
        raw = argv[0]
        # Reject flag-shaped intervals so e.g. ``schedule daemon --bogus``
        # surfaces a clean rc=2 instead of the bare ``invalid literal`` /
        # ``got: '--bogus'`` confusion. Same class as the consolidate run
        # / trace prune / trace list hardening (d51d613, eabb0f8).
        if raw.startswith("-") and not raw.lstrip("-").isdigit():
            print(
                f"schedule daemon: {raw!r} looks like a CLI flag, not an interval",
                file=sys.stderr,
            )
            return 2
        try:
            interval = int(raw)
        except ValueError:
            print(
                f"schedule daemon: interval expects an integer, got {raw!r}",
                file=sys.stderr,
            )
            return 2
        # A negative interval would propagate to ``time.sleep`` and crash
        # the loop on the first tick. Reject up-front so the operator
        # gets a CLI error, not a daemon traceback.
        if interval < 0:
            print(
                f"schedule daemon: interval must be non-negative, got {interval}",
                file=sys.stderr,
            )
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
    verb = "enable" if enabled else "disable"
    if not job_id:
        print(f"usage: schedule {verb} <job-id>", file=sys.stderr)
        return 2
    # Reject flag-shaped job refs so e.g. ``schedule enable --help``
    # surfaces a help/usage message instead of falling through to
    # ``job not found: --help``. Same class as df6812e (project init)
    # and 206c14b (groups create).
    if job_id in ("--help", "-h"):
        sys.stdout.write(USAGE)
        return 0
    if job_id.startswith("-"):
        print(
            f"schedule {verb}: {job_id!r} looks like a CLI flag, not a job id",
            file=sys.stderr,
        )
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
        sys.stdout.write(USAGE)
        return 0
    cmd = argv[0] if argv else "list"
    rest = argv[1:]

    if cmd in ("", "list", "ls"):
        project_arg = None
        extras = list(rest)
        if extras and not extras[0].startswith("-"):
            project_arg = extras.pop(0)
        if extras:
            head = extras[0]
            kind = "flag" if head.startswith("-") else "argument"
            sys.stderr.write(
                f"metasphere schedule list: unexpected {kind}: {head}\n"
                f"Usage: metasphere schedule list [project-name]\n"
            )
            return 2
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

    sys.stderr.write(USAGE)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
