"""CLI: ``metasphere schedule wire-exit-self``.

Idempotently appends a ``metasphere session exit-self`` cleanup stanza
to the ``payload_message`` of every cron job whose
``wants_exit_self_cleanup`` flag is True. Without the stanza, single-
shot persistent agents finish their work but their tmux session lingers
~24h until the next cron fire (the 'zombie session' pattern in
@explorer's 2026-04-28 reap log).

Companion to the merged fix at posthook.py:517-533 (commit 00fba07)
which made ``request_deferred_command``'s injection path
project-scope-aware. With the injection working, the next step is to
have flagged agents *call* ``metasphere session exit-self`` at the end
of their turn — that's the wiring this tool does.

Operators opt jobs in/out by editing the ``wants_exit_self_cleanup``
field in jobs.json. Persistent collaborators whose cold-start cost
exceeds the leak cost (e.g. polymarket:* + spot:autonomous-exploration)
should keep the flag at its default (``False``).

Usage::

    metasphere schedule wire-exit-self [--dry-run]
"""

from __future__ import annotations

import sys

from metasphere import paths as _paths
from metasphere import schedule as _sched

# Sentinel used by the idempotency check. If this substring already
# appears in a job's payload_message we assume the wiring has been done
# and skip. The literal command is the load-bearing instruction the
# agent must run, so it's a stable detector.
SENTINEL = "metasphere session exit-self"

CLEANUP_STANZA = (
    "---\n\n"
    "**Session cleanup (run last, after all delivery + !info messages):**\n\n"
    "Run `metasphere session exit-self` as your final action of the turn. "
    "This queues `/exit` for the next Stop-hook tick so this cron-fired "
    "session releases its tmux slot rather than idling ~24h until the "
    "next fire. Do this AFTER any `!info` to @orchestrator and AFTER "
    "any final substantive assistant text — it should be the literal "
    "last bash invocation. No further assistant text after the call "
    "(silent-tick `[idle]` is fine but not required)."
)


def _appended_payload(existing: str) -> str:
    """Return ``existing`` with the cleanup stanza appended.

    The stanza is separated from the existing body by a blank line —
    payloads vary in whether they end with a trailing newline, so we
    normalize by stripping trailing whitespace before joining.
    """
    body = (existing or "").rstrip()
    if not body:
        return CLEANUP_STANZA
    return f"{body}\n\n{CLEANUP_STANZA}"


def wire_exit_self(
    paths=None,
    *,
    dry_run: bool = False,
) -> dict[str, list[str]]:
    """Append the cleanup stanza to every flagged job's payload_message.

    Iterates jobs whose ``wants_exit_self_cleanup`` field is True.
    Idempotent: jobs whose payload already contains :data:`SENTINEL`
    are skipped. Operates under the same exclusive lock the cron daemon
    uses for ``last_fired_at`` writes, so it's race-safe against live
    fires.

    Returns a dict with two keys:
        ``modified`` — names of jobs whose payload was edited
        ``skipped``  — names of flagged jobs that already had the sentinel

    With ``dry_run=True`` the analysis runs but jobs.json is not
    written. The returned classification is identical to a real run.
    """
    paths = paths or _paths.resolve()

    modified: list[str] = []
    skipped: list[str] = []

    with _sched.with_locked_jobs(paths) as jobs:
        input_count = len(jobs)
        for job in jobs:
            if not job.wants_exit_self_cleanup:
                continue
            if SENTINEL in (job.payload_message or ""):
                skipped.append(job.name)
                continue
            job.payload_message = _appended_payload(job.payload_message)
            modified.append(job.name)
        if modified and not dry_run:
            _sched.save_jobs(jobs, paths, _input_count=input_count)

    return {
        "modified": sorted(modified),
        "skipped": sorted(skipped),
    }


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args and args[0] in ("--help", "-h"):
        print(__doc__ or "")
        return 0

    dry_run = "--dry-run" in args
    extras = [a for a in args if a not in ("--dry-run",)]
    if extras:
        print(f"unknown args: {extras}", file=sys.stderr)
        print(__doc__ or "", file=sys.stderr)
        return 2

    result = wire_exit_self(dry_run=dry_run)

    label = "would modify" if dry_run else "modified"
    print(f"{label}: {len(result['modified'])} job(s)")
    for name in result["modified"]:
        print(f"  + {name}")
    print(f"skipped (already wired): {len(result['skipped'])} job(s)")
    for name in result["skipped"]:
        print(f"  = {name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
