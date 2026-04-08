---
id: msg-1775530663-98146
from: @orchestrator
to: @orchestrator
label: !done
status: unread
scope: /
created: 2026-04-07T02:57:43Z
read_at:
replied_at:
completed_at:
reply_to: 
---

AUDIT: Fixed 2 line-shredder bugs in scripts/metasphere-session (line 188 $init_prompt multi-line, line 245 $message potentially multi-line) using load-buffer/paste-buffer pattern. Audited all send-keys call sites: metasphere-gateway:218,272 (single-line/Enter only - safe), metasphere-session:182 $claude_cmd (single-line - safe), :295 /exit (safe), heartbeat:187 (already fixed). No 'while read' loops feeding send-keys found. Synced metasphere-session, metasphere-heartbeat, metasphere-gateway to spot at /var/lib/machines/openclaw/home/openclaw/.metasphere/bin/ with proper ownership. Restarted metasphere-telegram.service on spot (active/running). Test telegram 'audit done' sent successfully (message_id 17062). Committed as 69fc40a.
