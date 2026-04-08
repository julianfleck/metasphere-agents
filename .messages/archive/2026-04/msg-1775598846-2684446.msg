---
id: msg-1775598846-2684446
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T21:54:06Z
read_at:
replied_at:
completed_at:
reply_to: 
---

Unify the metasphere CLI surface into a single `metasphere` entry-point with subcommands. Currently 13+ sibling `metasphere-*` console-script entries in pyproject.toml; Julian wants one mental model: `metasphere hooks posthook`, `metasphere telegram send`, `metasphere agent spawn`, etc. Branch directly on main, one commit per logical step, do NOT push.

DESIGN

One [project.scripts] entry: `metasphere = metasphere.cli.main:main`

`metasphere.cli.main:main` is a thin top-level argparse dispatcher. It does NOT import every submodule eagerly — each subcommand lazy-imports its handler so startup stays fast (sub-50ms). Use a registry dict mapping subcommand name → 'metasphere.cli.<module>:<func>' import path, resolve via importlib at dispatch time.

Subcommand layout (mirror existing modules; no behavior changes in this tranche, just routing):

  metasphere status                  # existing top-level (stays)
  metasphere ls [path]               # existing
  metasphere agent spawn ...         # was metasphere-spawn
  metasphere agent wake ...          # was metasphere-wake
  metasphere agent list              # was metasphere-agent list
  metasphere agent status            # was metasphere-agent status
  metasphere msg send|list|done|reply|all     # was `messages` shim
  metasphere task new|start|update|done|list  # was `tasks` shim
  metasphere telegram send|poll|once|getme    # was metasphere-telegram
  metasphere telegram groups ...              # was metasphere-telegram-groups
  metasphere hooks posthook                   # was metasphere-posthook
  metasphere hooks context                    # was metasphere-context
  metasphere hooks git install|uninstall|status   # was metasphere-git-hooks
  metasphere schedule list|run|daemon         # was metasphere-schedule
  metasphere heartbeat tick|daemon            # was metasphere-heartbeat
  metasphere memory search|index              # was metasphere-memory / metasphere-fts
  metasphere trace capture|list|search|prune  # was metasphere-trace
  metasphere session ...                      # was metasphere-session
  metasphere project ...                      # was metasphere-project
  metasphere gateway status|daemon            # was metasphere-gateway

DEPRECATION SHIMS

Keep all existing `metasphere-*` console-script entries in pyproject.toml. Each entry now points at a thin shim function in metasphere.cli.<module> that:
  1. Prints a one-line deprecation notice to stderr ("metasphere-foo is deprecated; use 'metasphere foo' instead") — but ONLY when stderr is a TTY, so systemd/hook callers don't get spammed.
  2. Forwards argv to metasphere.cli.main with the new subcommand prefix prepended.

This way nothing breaks: hooks/systemd/the orchestrator's shell aliases keep working until they're individually updated. Add a follow-up task to migrate the 4 known callers (.claude/settings.local.json hooks + 3 systemd unit files) in a separate commit so this tranche stays focused.

CONSTRAINTS

- Tests stay green: `pytest metasphere/tests/ -q` → 188+ passed, 4 deselected.
- Add a metasphere/tests/test_cli_dispatch.py with cases for: top-level --help lists all subcommands; each subcommand --help works; an unknown subcommand exits 2 with usage; the lazy-import registry resolves every entry without ImportError; old `metasphere-foo` shims still work and print the deprecation notice on TTY only.
- No behavior changes inside any subcommand handler — they all keep their existing argparse, just one extra layer of dispatch in front. If you discover a behavior change is needed (e.g. an arg name conflict), STOP and message back; don't paper over it.
- Update docs/CLI.md to document the new layout. Old per-script section can become a 'deprecated shims' appendix.
- One commit per logical step (recommend: 1=registry+main dispatcher, 2=migrate hooks, 3=migrate agent/msg/task, 4=migrate telegram/schedule/heartbeat/memory/trace/etc, 5=docs+test_cli_dispatch). 5 commits total. NO push.
- Do NOT touch the systemd unit files or .claude/settings.local.json in this tranche — those go in a follow-up so the unification can land cleanly first.
- Time budget: aggressive. If a subcommand resists clean dispatch, leave the old shim in place + document the holdout in the report; don't burn an hour on edge cases.

When done: write ~/.metasphere/agents/@cli-unify/report.md with commit hashes, subcommand coverage table (was → is), test count delta, and any deferrals. Then `messages send @.. !done <verdict>`.

NOTE: there is a parallel posthook dedupe fix already on main (commit 7651ecb) — it touches metasphere/posthook.py and metasphere/cli/telegram.py. Rebase on main before starting.
