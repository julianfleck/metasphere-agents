---
id: msg-1775597509-2680557
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T21:31:49Z
read_at:
replied_at:
completed_at:
reply_to: 
---

Land all 5 e2e findings from ~/.metasphere/agents/@e2e-systems-check/report.md as one tranche on main. Tests must stay green (172/172). Commit per fix with a clear message; do NOT push.

The findings:

F1 (lite-blocker) — metasphere/messages.py around line 317: 'messages send @/abs/path/' resolves the leading slash relatively, producing doubled-prefix paths like /tmp/scope/tmp/scope/.messages/inbox/. Fix: when target starts with @/, strip the @ and treat the remainder as an absolute filesystem path; do NOT join with current scope. Add a unit test in metasphere/tests/test_messages.py covering @/abs/path/ targeting.

F2 — uniform --help/-h handling. CLIs that currently reject --help: messages, tasks, agents, heartbeat, schedule, trace, session, project. Find the dispatcher pattern (probably in metasphere/cli/<name>.py main() functions) and ensure each main() accepts --help/-h and prints argparse usage cleanly with rc=0. Some already work (context, posthook, telegram, gateway, memory) — mirror their pattern.

F3 — trace spec/impl drift. CLI subcommands are 'capture | list | search | prune'; spec said 'emit'. Artifacts land in ~/.metasphere/traces/, not ~/.metasphere/logs/trace.jsonl. Pick the smallest path: update docs (CLAUDE.md, docs/CLI.md, any task templates that say 'trace emit') to match the impl. Do NOT rename the artifact directory — too many readers depend on ~/.metasphere/traces/. Just align documentation.

F4 — metasphere-git-hooks install: add a --dry-run flag that prints the symlink/copy operations it would perform without executing them. The script lives at scripts/metasphere-git-hooks (or metasphere/cli/git_hooks.py — check both).

F5 — metasphere-posthook: add --help (argparse) and --dry-run. In dry-run mode, parse the stdin payload as usual but skip the actual telegram send and the actual file writes; print a JSON summary of what would have been sent (target chat, text length, chunk count). This unblocks future e2e rigs.

Constraints:
- One commit per fix (5 commits total). Commit messages should reference the F-number.
- Run pytest after each commit; if any test breaks, fix or revert before moving on.
- Add tests where reasonable (definitely for F1; F2/F4/F5 are more CLI-shape tests).
- Do NOT push. Do NOT touch real ~/.metasphere/ state during testing — use tmp_path / fixtures.
- Time budget: aggressive. If a fix takes >15min of debugging, document the blocker and skip to the next.

When done: write ~/.metasphere/agents/@fixer-yellow/report.md with the commit hashes for each F, the test count delta, and any skipped items. Then messages send @.. !done with a one-line verdict.
