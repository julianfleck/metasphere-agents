---
id: msg-1775649121-2864030
from: @lifecycle-hardening
to: @orchestrator
label: !done
status: read
scope: /
created: 2026-04-08T11:52:01Z
read_at: 2026-04-08T11:56:33Z
replied_at: 
completed_at: 
reply_to: 
last_pinged_at: 
ping_count: 0
---
@lifecycle-hardening complete. Two commits landed on main:

b78fce8 tasks: require project+assignee; add assign/move/list filters
6d0774f format: shared ASCII+emoji table for tasks/schedule list

ASK 1 — assignment hardening:
- Task model gains 'project' field; create_task auto-fills project via project_for_scope (fallback 'default') and assignee from METASPHERE_AGENT_ID.
- metasphere-spawn now passes assigned_to=agent_id so ephemeral children stop leaking unowned tasks (the 2026-04-08 audit root cause).
- New subcommands: tasks new --project/--assign (soft stderr warning on auto-fill miss), tasks assign <id> @agent, tasks move <id> --project <name>.
- tasks list gains --unassigned / --project / --owner filters.
- Legacy frontmatter without 'project:' loads as 'default'.

ASK 2 — formatting:
- New metasphere/format.py; shared renderer for tasks + schedule.
- Plain ASCII pipe tables (no unicode box drawing), colored-circle emojis rendered natively by Telegram: 🔵 pending 🟡 in-progress 🔴 blocked 🟢 completed 🟣 stale ⚪ unowned; 🟢/🔴 schedule enabled/disabled.
- Timestamps formatted as YYYY-MM-DD HH:MM UTC; titles/ids ellipsized; next-fire computed via croniter.
- Telegram /tasks and /schedule force METASPHERE_PLAIN=1 in-process around the CLI dispatch.

Tests: 326 passing (up from 309; +17 new). Smoke-tested both CLIs against the real backlog and schedule — output renders cleanly.