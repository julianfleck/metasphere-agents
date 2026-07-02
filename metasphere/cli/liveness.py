"""``metasphere liveness`` — who is actually generating / idle / stale, now.

A read-only diagnostic surface over :mod:`metasphere.liveness`: it tails each
agent's tmux pane, diffs it against the cached snapshot, and prints a
project-grouped "who's working" view. This is the operator/dev probe — the
Telegram ``/status`` redesign (one ``/status`` vs. a split ``/active``) is a
separate, still-open product decision and is intentionally not wired here.

Because liveness is computed by diffing two captures, the freshness of the
``generating``/``idle`` split depends on a prior snapshot existing: the first
run after a cold start reports ``unknown`` until a second capture lands. Run it
twice (or let the heartbeat warm the cache) for a settled reading.
"""

from __future__ import annotations

import json
import sys

from metasphere.liveness import format_liveness, liveness_snapshot

DESCRIPTION = "Show which agents are generating / idle / stale right now (tmux pane freshness)."

USAGE = """\
Usage: metasphere liveness [--json] [--all] [--include-dead]

Probe every persistent agent's tmux pane and classify it:
  ● generating   pane output moved since the last capture, or the Claude TUI
                 footer shows `esc to interrupt` (a turn/tool is running).
  ○ idle         alive, pane unchanged within the stale window.
  ◐ stale        alive, pane unchanged longer than the stale window (possible
                 hang). Default window: 600s (METASPHERE_LIVENESS_STALE_AFTER_S).
  · unknown      alive but no prior snapshot yet (cold start) — run again.
  · dead         no tmux session.

Options:
  --json           Emit one JSON object per agent instead of the grouped view.
  --all            Include ephemeral agents, not just persistent team agents.
  --include-dead   Include agents whose tmux session is gone.

Note: the first run after a cold start reports `unknown` because liveness is a
diff between two captures. Run twice, or let the heartbeat warm the cache.

Thresholds are env-overridable:
  METASPHERE_LIVENESS_STALE_AFTER_S   idle→stale boundary (default 600)
  METASPHERE_LIVENESS_TAIL_LINES      pane lines hashed/scanned (default 6)
"""


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if "--help" in args or "-h" in args:
        sys.stdout.write(USAGE)
        return 0

    as_json = "--json" in args
    persistent_only = "--all" not in args
    include_dead = "--include-dead" in args

    items = liveness_snapshot(
        persistent_only=persistent_only,
        include_dead=include_dead,
    )

    if as_json:
        for lv in items:
            print(json.dumps({
                "agent": lv.agent,
                "project": lv.project,
                "session": lv.session,
                "state": lv.state,
                "idle_age_s": lv.idle_age_s,
                "doing": lv.doing,
            }))
        return 0

    print(format_liveness(items))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
