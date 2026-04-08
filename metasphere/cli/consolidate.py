"""``metasphere consolidate`` CLI.

Subcommand surface::

    metasphere consolidate run [--dry-run] [--since 2d] [--stale-window 15]
    metasphere consolidate --register-job
    metasphere consolidate --unregister-job
    metasphere consolidate --status

The ``run`` subcommand walks every active task under the repo and
classifies each into one of five lifecycle verdicts
(ACTIVE / STALE / BLOCKED / UNOWNED / DONE) — see
:mod:`metasphere.consolidate` for the rules and the corresponding
actions (ping, escalate, archive).
"""

from __future__ import annotations

import sys

from metasphere import consolidate as _con
from metasphere import paths as _paths
from metasphere import schedule as _sched

_HELP = __doc__ or ""


def _cmd_run(argv: list[str]) -> int:
    dry_run = False
    since = _con.DEFAULT_SINCE
    stale_window = _con.STALE_WINDOW_MINUTES_DEFAULT
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == "--dry-run":
            dry_run = True
        elif a == "--since":
            i += 1
            since = argv[i]
        elif a.startswith("--since="):
            since = a.split("=", 1)[1]
        elif a == "--stale-window":
            i += 1
            stale_window = int(argv[i])
        elif a.startswith("--stale-window="):
            stale_window = int(a.split("=", 1)[1])
        else:
            print(f"unknown arg: {a}", file=sys.stderr)
            return 2
        i += 1

    paths = _paths.resolve()
    report = _con.run_pass(
        repo_root=paths.repo,
        since=since,
        stale_window_minutes=stale_window,
        dry_run=dry_run,
        paths=paths,
    )

    mode = "dry-run" if dry_run else "live"
    print(
        f"consolidate ({mode}, since={since}, stale_window={stale_window}m): "
        f"{len(report.results)} tasks scanned"
    )
    for r in report.results:
        marker = f"[{r['action']}]"
        line = f"  {marker:32s} {r['verdict']:8s} {r['task_id']}"
        if r.get("target"):
            line += f"  → {r['target']}"
        print(line)
    counts = report.counts()
    if counts:
        summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        print(f"summary: {summary}")

    if report.message_results:
        print(f"messages scanned: {len(report.message_results)}")
        for r in report.message_results:
            if r["action"] == "noop":
                continue
            marker = f"[{r['action']}]"
            line = f"  {marker:32s} {r['verdict']:24s} {r['msg_id']}"
            if r.get("target"):
                line += f"  → {r['target']}"
            print(line)
        mcounts = report.message_counts()
        if mcounts:
            msummary = ", ".join(f"{k}={v}" for k, v in sorted(mcounts.items()))
            print(f"message summary: {msummary}")
    return 0


def _cmd_register() -> int:
    paths = _paths.resolve()
    job = _con.register_job(paths)
    print(f"task:consolidate cron job registered ({job.cron_expr}, enabled={job.enabled})")
    return 0


def _cmd_unregister() -> int:
    paths = _paths.resolve()
    if _con.unregister_job(paths):
        print("task:consolidate cron job removed")
        return 0
    print("task:consolidate cron job was not registered")
    return 0


def _cmd_status() -> int:
    paths = _paths.resolve()
    try:
        jobs = _sched.load_jobs(paths)
    except Exception as e:
        print(f"error reading jobs: {e}", file=sys.stderr)
        return 1
    job = next((j for j in jobs if j.id == _con.JOB_ID), None)
    if job is None:
        print("task:consolidate cron job: (not registered)")
    else:
        print(f"task:consolidate cron job: {job.cron_expr} (enabled={job.enabled})")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        sys.stdout.write(_HELP)
        return 0
    head, rest = argv[0], argv[1:]
    if head == "run":
        return _cmd_run(rest)
    if head == "--register-job":
        return _cmd_register()
    if head == "--unregister-job":
        return _cmd_unregister()
    if head == "--status":
        return _cmd_status()
    print(f"metasphere consolidate: unknown subcommand: {head}", file=sys.stderr)
    sys.stderr.write(_HELP)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
