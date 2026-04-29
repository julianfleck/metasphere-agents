#!/usr/bin/env python3
"""One-time migration: set ``wants_exit_self_cleanup=True`` on legacy jobs.

Background: ``metasphere/cli/wire_exit_self.py`` used to filter cron
jobs via a hardcoded ``TARGET_JOB_NAMES`` tuple of 12 spot-deployed
job names. The plan is to remove that allow-list and replace it with a
per-job boolean flag ``wants_exit_self_cleanup`` on the ``Job``
dataclass — operators opt jobs in/out without editing library code.

This script writes ``wants_exit_self_cleanup=True`` into each of the
12 historically-targeted jobs in spot's ``jobs.json``. After running
it, ``wire_exit_self`` continues to wire the same set of jobs even
after the library-side ``TARGET_JOB_NAMES`` constant is removed.

Idempotent: jobs already flagged True are skipped; non-target jobs are
left alone.

Safety: backs up ``jobs.json`` to ``<jobs.json>.bak-pre-exit-self-flag-<unix>``
before any write. ``--dry-run`` prints the planned diff without
touching disk or backing up.

Usage::

    python3 scripts/migrate_schedule_exit_self_flag.py --dry-run
    python3 scripts/migrate_schedule_exit_self_flag.py
    python3 scripts/migrate_schedule_exit_self_flag.py --jobs-path /custom/jobs.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path


# The 12 historical TARGET_JOB_NAMES values, frozen here so this script
# remains useful even after the library-side constant is removed.
LEGACY_TARGET_NAMES: frozenset[str] = frozenset({
    "Morning briefing",
    "rage-changelog-update",
    "research-monitor:brand-mentions",
    "research-monitor:memory-architectures",
    "research-monitor:retrieval-architectures",
    "research-monitor:agentic-reasoning",
    "research-monitor:evaluation-governance",
    "research-monitor:divergence-engines",
    "research-monitor:ephemeral-interfaces",
    "research-monitor:residency-programs",
    "research-monitor:job-opportunities",
    "research-monitor:accelerator-programs",
})


def default_jobs_path() -> Path:
    metasphere_dir = os.environ.get("METASPHERE_DIR") or str(Path.home() / ".metasphere")
    return Path(metasphere_dir) / "schedule" / "jobs.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true",
                        help="print planned changes without writing")
    parser.add_argument("--jobs-path", default=None,
                        help="override $METASPHERE_DIR/schedule/jobs.json path")
    args = parser.parse_args(argv)

    jobs_path = Path(args.jobs_path) if args.jobs_path else default_jobs_path()
    if not jobs_path.is_file():
        print(f"error: jobs.json not found at {jobs_path}", file=sys.stderr)
        return 1

    with open(jobs_path, encoding="utf-8") as f:
        jobs = json.load(f)
    if not isinstance(jobs, list):
        print(f"error: expected list at {jobs_path}, got {type(jobs).__name__}",
              file=sys.stderr)
        return 1

    rewrites = []  # (index, name)
    for i, job in enumerate(jobs):
        name = job.get("name") or ""
        if name not in LEGACY_TARGET_NAMES:
            continue
        if job.get("wants_exit_self_cleanup") is True:
            continue  # already migrated
        rewrites.append((i, name))

    print(f"jobs.json: {jobs_path}")
    print(f"total jobs: {len(jobs)}")
    print(f"jobs needing wants_exit_self_cleanup=True: {len(rewrites)}")
    print()

    if not rewrites:
        print("no-op: all 12 legacy targets already flagged (or absent).")
        return 0

    print("planned rewrites:")
    for i, name in rewrites:
        print(f"  [{i:2d}] {name}")
    print()

    if args.dry_run:
        print("--dry-run: no changes written.")
        return 0

    ts = int(time.time())
    backup = jobs_path.with_name(jobs_path.name + f".bak-pre-exit-self-flag-{ts}")
    shutil.copy2(jobs_path, backup)
    print(f"backup written to {backup}")

    for i, _ in rewrites:
        jobs[i]["wants_exit_self_cleanup"] = True
    with open(jobs_path, "w", encoding="utf-8") as f:
        json.dump(jobs, f, indent=2)
        f.write("\n")
    print(f"wrote {len(rewrites)} flag updates to {jobs_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
