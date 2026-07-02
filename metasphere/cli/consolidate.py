"""``metasphere consolidate`` — sweep stale tasks and messages.

Thin shim over ``metasphere.consolidate``; one invocation per
heartbeat-style sweep. Classifies active tasks and unread messages into
buckets (stale / unowned / info-auto-archive / escalate) and applies
the resulting transitions, emitting one ``task.consolidate`` /
``message.consolidate`` event per action. Designed to be cron-driven —
behaviour is idempotent across reruns within a tick window.
"""

from __future__ import annotations

import sys

from metasphere import consolidate as _con
from metasphere import paths as _paths
from metasphere import schedule as _sched


DESCRIPTION = "Sweep active tasks: classify, ping, escalate, archive."

USAGE = """\
Usage: metasphere consolidate <command> [args...]

Commands:
  run [--dry-run] [--since <window>] [--stale-window <minutes>]
      [--info-archive-after <minutes>]
                          Walk every active task under the project
                          root and classify each into ACTIVE / STALE /
                          BLOCKED / UNOWNED / DONE. Issue the matching
                          action (ping, escalate, archive) unless
                          --dry-run is set.
  --register-job          Register the cron job for this consolidator.
  --unregister-job        Remove the cron job.
  --status                Print whether the cron job is registered and
                          its schedule.

Defaults:
  --since                 Recent-window for stale detection.
  --stale-window          Minutes a task can sit idle before STALE
                          fires.
"""


def _parse_int_flag(
    flag: str, raw: str, *, allow_negative: bool = False
) -> int:
    """Parse an integer value for ``--flag`` with CLI-shaped errors.

    Raises ``ValueError`` with a message ready for stderr. Callers catch
    and return rc=2. Centralises the flag-shape / non-int / negative
    rejections that trace.prune / trace.list already do inline so the
    behaviour is consistent across consolidate's two int-valued flags.
    """
    if raw.startswith("-") and not raw.lstrip("-").isdigit():
        raise ValueError(
            f"consolidate run: {raw!r} looks like a CLI flag, not a "
            f"value for {flag}"
        )
    try:
        n = int(raw)
    except ValueError:
        raise ValueError(
            f"consolidate run: {flag} expects an integer, got {raw!r}"
        )
    if not allow_negative and n < 0:
        raise ValueError(
            f"consolidate run: {flag} must be non-negative, got {n}"
        )
    return n


def _take_value(flag: str, argv: list[str], i: int) -> str:
    """Return the value following ``--flag`` at position ``i+1``.

    Raises ``ValueError`` if the flag was the last token. Same shape as
    ``_parse_int_flag`` so the caller can render a single rc=2 path.
    """
    if i + 1 >= len(argv):
        raise ValueError(f"consolidate run: {flag} expects a value")
    return argv[i + 1]


def _cmd_run(argv: list[str]) -> int:
    dry_run = False
    since = _con.DEFAULT_SINCE
    stale_window = _con.STALE_WINDOW_MINUTES_DEFAULT
    info_archive_after: int | None = None
    i = 0
    try:
        while i < len(argv):
            a = argv[i]
            if a == "--dry-run":
                dry_run = True
            elif a in ("--help", "-h"):
                sys.stdout.write(USAGE)
                return 0
            elif a == "--since":
                since = _take_value("--since", argv, i)
                i += 1
            elif a.startswith("--since="):
                since = a.split("=", 1)[1]
            elif a == "--stale-window":
                stale_window = _parse_int_flag(
                    "--stale-window", _take_value("--stale-window", argv, i)
                )
                i += 1
            elif a.startswith("--stale-window="):
                stale_window = _parse_int_flag(
                    "--stale-window", a.split("=", 1)[1]
                )
            elif a == "--info-archive-after":
                info_archive_after = _parse_int_flag(
                    "--info-archive-after",
                    _take_value("--info-archive-after", argv, i),
                )
                i += 1
            elif a.startswith("--info-archive-after="):
                info_archive_after = _parse_int_flag(
                    "--info-archive-after", a.split("=", 1)[1]
                )
            else:
                print(f"unknown arg: {a}", file=sys.stderr)
                return 2
            i += 1
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    paths = _paths.resolve()
    # The info_archive_after override is threaded through a module-level
    # default so the classifier can pick it up without changing the
    # run_pass signature. It's reset after the call for test isolation.
    prev_info = _con.INFO_AUTO_ARCHIVE_AFTER_MINUTES
    if info_archive_after is not None:
        _con.INFO_AUTO_ARCHIVE_AFTER_MINUTES = info_archive_after
    try:
        report = _con.run_pass(
            project_root=paths.project_root,
            since=since,
            stale_window_minutes=stale_window,
            dry_run=dry_run,
            paths=paths,
        )
    finally:
        _con.INFO_AUTO_ARCHIVE_AFTER_MINUTES = prev_info

    mode = "dry-run" if dry_run else "live"
    print(
        f"consolidate ({mode}, since={since}, stale_window={stale_window}m): "
        f"{len(report.results)} tasks scanned"
    )
    for r in report.results:
        icon = "○" if r["action"] == "noop" else "●"
        action = r["action"]
        line = f"  {icon} {action:28s} {r['verdict']:8s} {r['task_id']}"
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
            icon = "●"
            action = r["action"]
            line = f"  {icon} {action:28s} {r['verdict']:24s} {r['msg_id']}"
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
        sys.stdout.write(USAGE)
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
    sys.stderr.write(USAGE)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
