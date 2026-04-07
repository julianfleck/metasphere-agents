---
id: msg-1775530081-69498
from: @orchestrator
to: @orchestrator
label: !done
status: unread
scope: /
created: 2026-04-07T02:48:01Z
read_at:
replied_at:
completed_at:
reply_to: 
---

UX FIXES R2: Fixed all 6 bugs. (1) metasphere-context: removed invalid 'local' keywords at script scope (stderr now silent). (2) chmod +x on metasphere-agent, metasphere-git-hooks, metasphere-migrate, metasphere-project, metasphere-session, metasphere-trace. (3) messages send: redirected stdout of metasphere-events/metasphere-agent subcalls to /dev/null to prevent ANSI/output leak through pipes (TTY guard for own colors was already present). (4) metasphere-events tail: strip newlines and truncate .message to 80 chars, pipe through head -n N to guarantee N entries. (5) metasphere-agent tree: added 'Unlinked agents' section tracking printed set; now shows 7 previously-hidden agents (heartbeat-fixer, hook-dev, identity-fixer, memory-dev, messaging-dev, spot-deployer, ux-tester). (6) metasphere-context: reordered so Agent Identity comes first (after tiny header), then Telegram (truncated to 3 msgs / 1KB max). Verified: stderr silent, output starts with Agent Identity, ~600ms runtime. Committed as 28946da.
