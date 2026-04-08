"""``metasphere consolidate`` CLI.

Subcommand surface::

    metasphere consolidate run [--dry-run] [--since 7d] [--threshold high|medium|low]
    metasphere consolidate --register-job
    metasphere consolidate --unregister-job
    metasphere consolidate --status

The ``run`` subcommand walks every active task under the repo, asks
``git log`` for evidence in the recent window, and either archives
high-confidence matches, annotates medium-confidence ones, or leaves
the rest alone. See :mod:`metasphere.consolidate` for the matching
rules.
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
    threshold = _con.DEFAULT_THRESHOLD
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
        elif a == "--threshold":
            i += 1
            threshold = argv[i]
        elif a.startswith("--threshold="):
            threshold = a.split("=", 1)[1]
        else:
            print(f"unknown arg: {a}", file=sys.stderr)
            return 2
        i += 1

    if threshold not in _con.VERDICT_ORDER:
        print(f"invalid --threshold {threshold!r}; want high|medium|low", file=sys.stderr)
        return 2

    paths = _paths.resolve()
    report = _con.run_pass(
        repo_root=paths.repo,
        since=since,
        threshold=threshold,
        dry_run=dry_run,
        paths=paths,
    )

    mode = "dry-run" if dry_run else "live"
    print(f"consolidate ({mode}, since={since}, threshold={threshold}): {len(report.results)} tasks scanned")
    for r in report.results:
        marker = {
            "archived": "[ARCHIVE]",
            "would-archive": "[would-archive]",
            "annotated": "[ANNOTATE]",
            "would-annotate": "[would-annotate]",
            "noop": "[skip]",
        }.get(r["action"], f"[{r['action']}]")
        line = f"  {marker:18s} {r['verdict']:6s} {r['task_id']}"
        if r["sha"]:
            line += f"  ({r['sha']})"
        print(line)
        if r["note"] and r["action"] in ("archived", "would-archive", "annotated", "would-annotate"):
            print(f"      note: {r['note']}")
    counts = report.counts()
    if counts:
        summary = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
        print(f"summary: {summary}")
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
