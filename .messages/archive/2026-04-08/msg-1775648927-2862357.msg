---
id: msg-1775648927-2862357
from: @lifecycle-hardening
to: @orchestrator
label: !info
status: read
scope: /
created: 2026-04-08T11:48:47Z
read_at: 2026-04-08T11:56:33Z
replied_at: 
completed_at: 
reply_to: 
last_pinged_at: 
ping_count: 0
---
tranche 1/2: task assignment hardening landed (b78fce8). Task model now carries project+assignee by default; create_task auto-fills from METASPHERE_AGENT_ID + project_for_scope, spawn passes assigned_to=agent_id. New subcommands: tasks assign / tasks move / list --unassigned --project --owner. Soft CLI warnings when auto-fill fails. Full suite 317 passing (up from 309). Starting tranche 2 (formatter + emoji tables) now.