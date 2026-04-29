#!/usr/bin/env python3
"""One-time migration: write resolved agent_id into jobs.json.

Background: the legacy ``schedule.resolve_target_agent`` function uses
hardcoded name-prefix branches (``research-monitor:``, ``polymarket:``,
``spot:autonomous-exploration``, ``rage-changelog``, ``Morning briefing``)
to override a job's ``agent_id`` field at fire time. The plan is to
remove those branches, making ``agent_id`` the sole source of truth.
This script writes the prefix-match-resolved agent name into each
matching job's ``agent_id`` field so that the simpler post-migration
code (``return "@" + (job.agent_id or "main")``) returns the same
target agent per job.

Idempotent: jobs whose ``agent_id`` already matches the prefix-match
target are skipped.

Safety: backs up ``jobs.json`` to ``<jobs.json>.bak-pre-agent-id-migration-<unix>``
before any write. ``--dry-run`` prints the planned diff without
touching disk or backing up.

Usage::

    python3 scripts/migrate_schedule_agent_ids.py --dry-run
    python3 scripts/migrate_schedule_agent_ids.py
    python3 scripts/migrate_schedule_agent_ids.py --jobs-path /custom/jobs.json
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path


def legacy_resolve_target_agent(name: str, agent_id: str) -> str:
    """Replicate the legacy prefix-match branches verbatim.

    Mirrors ``metasphere.schedule.resolve_target_agent`` as of 2026-04-29.
    Returns the agent name **without** the leading ``@``.
    """
    if name.startswith("research-monitor:"):
        return name[len("research-monitor:"):]
    if name.startswith("polymarket:"):
        return "polymarket"
    if name.startswith("spot:autonomous-exploration"):
        return "explorer"
    if name.startswith("rage-changelog"):
        return "rage-changelog"
    if name.startswith("Morning briefing"):
        return "briefing"
    return agent_id or "main"


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

    rewrites = []  # (index, job-name, old-agent_id, new-agent_id)
    for i, job in enumerate(jobs):
        name = job.get("name") or ""
        old_id = job.get("agent_id") or "main"
        new_id = legacy_resolve_target_agent(name, old_id)
        if new_id != old_id:
            rewrites.append((i, name, old_id, new_id))

    print(f"jobs.json: {jobs_path}")
    print(f"total jobs: {len(jobs)}")
    print(f"jobs needing rewrite: {len(rewrites)}")
    print()

    if not rewrites:
        print("no-op: all jobs already have explicit agent_id matching legacy resolution.")
        return 0

    print("planned rewrites:")
    for i, name, old_id, new_id in rewrites:
        print(f"  [{i:2d}] {name:50s}  agent_id: {old_id!r:15s} -> {new_id!r}")
    print()

    if args.dry_run:
        print("--dry-run: no changes written.")
        return 0

    # Backup before write.
    ts = int(time.time())
    backup = jobs_path.with_name(jobs_path.name + f".bak-pre-agent-id-migration-{ts}")
    shutil.copy2(jobs_path, backup)
    print(f"backup written to {backup}")

    # Apply rewrites and save.
    for i, _, _, new_id in rewrites:
        jobs[i]["agent_id"] = new_id
    with open(jobs_path, "w", encoding="utf-8") as f:
        json.dump(jobs, f, indent=2)
        f.write("\n")
    print(f"wrote {len(rewrites)} rewrites to {jobs_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
