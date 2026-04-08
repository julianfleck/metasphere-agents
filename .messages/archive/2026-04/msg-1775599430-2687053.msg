---
id: msg-1775599430-2687053
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T22:03:50Z
read_at:
replied_at:
completed_at:
reply_to: 
---

Migrate the 4 known callers from legacy metasphere-* console scripts to the unified `metasphere <subcommand>` form. Branch directly on main, one commit per caller, NO push. Tests stay green (216 passed, 4 deselected).

THE 4 CALLERS

1. .claude/settings.local.json — Stop and UserPromptSubmit hooks. Currently: `metasphere-posthook` and `metasphere-context`. New: `metasphere hooks posthook` and `metasphere hooks context`. Read the existing JSON, update only the .hooks.{Stop,UserPromptSubmit}[].hooks[].command fields, leave everything else byte-identical. Validate the JSON parses after editing.

2. ~/.config/systemd/user/metasphere-heartbeat.service — ExecStart line. Currently: `/home/openclaw/.openclaw/workspace/repos/rage-substrate/.venv/bin/metasphere-heartbeat daemon 300`. New: `/home/openclaw/.openclaw/workspace/repos/rage-substrate/.venv/bin/metasphere heartbeat daemon 300`. Note: this file is OUTSIDE the repo (it lives in ~/.config/systemd/user/). Edit it in place, then `systemctl --user daemon-reload` and `systemctl --user restart metasphere-heartbeat.service`. Verify it comes back active before moving on.

3. ~/.config/systemd/user/metasphere-schedule.service — same drill, ExecStart `metasphere-schedule daemon` → `metasphere schedule daemon`. daemon-reload + restart + verify active.

4. ~/.config/systemd/user/metasphere-telegram.service — same drill, ExecStart `metasphere-telegram poll` → `metasphere telegram poll`. daemon-reload + restart + verify active.

You are NOT migrating ~/.metasphere/bin/* shims — those are user-level conveniences, separate cleanup.

CONSTRAINTS

- Backup each file before editing. Stage backups under ~/.metasphere/backup/caller-migration-<DATE>/. If anything looks weird in the new state, revert from the backup, don't try to forward-fix.
- After ALL 4 callers are migrated, run pytest one more time as a sanity check, and run `metasphere telegram getme` to confirm the canonical bot is still reached. Then `systemctl --user list-units --state=active --type=service | grep metasphere` to confirm all 3 services are active.
- After service restarts, the metasphere-telegram poll daemon must be the only thing polling getUpdates — if you see Conflict errors in ~/.metasphere/logs/telegram-stream.log within 30s of restart, STOP and report.
- Hooks are riskier than systemd because the next claude-code Stop event will use the new path immediately. Test the new hooks command path first via a manual invocation: `echo '{}' | metasphere hooks posthook --dry-run` should print a JSON plan, exit 0. `metasphere hooks context` should print a context block. Only edit settings.local.json AFTER both manual commands work.
- One commit per caller (4 commits). Commit messages should reference what was changed and why (e.g. 'caller-migration 1/4: .claude hooks → metasphere hooks {posthook,context}').
- No push.

When done: write ~/.metasphere/agents/@caller-migration/report.md with a before→after table, the systemd `status` excerpt for each service (1-line is fine, just confirming Active: active), and any hiccups encountered. Then `messages send @.. !done <verdict>`.

DEFERRAL

Do NOT retire the legacy metasphere-* console-script entries from pyproject.toml in this tranche — that's a follow-up after we've watched the new wiring run for at least a day. Just point the callers at the new commands; the old entries stay as fallback shims for now.
