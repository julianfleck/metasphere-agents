---
id: msg-1775529781-3914
from: @orchestrator
to: @orchestrator
label: !done
status: unread
scope: /
created: 2026-04-07T02:43:01Z
read_at:
replied_at:
completed_at:
reply_to: 
---

IDENTITY FIX: added scripts/metasphere-identity helper with resolution order (env > ~/.metasphere/current_agent > @orchestrator > @user); wired into messages, tasks, metasphere-events; wrote ~/.metasphere/current_agent=@orchestrator; fixed metasphere-heartbeat no-args to run single check. Verified: message.send event now credits @orchestrator.
