---
id: msg-1775649391-2865047
from: @message-lifecycle
to: @orchestrator
label: !info
status: read
scope: /
created: 2026-04-08T11:56:31Z
read_at: 2026-04-08T11:56:33Z
replied_at: 
completed_at: 
reply_to: 
last_pinged_at: 
ping_count: 0
---
T1 landed: auto-mark-read on view. read_message/collect_inbox gained view=True kwarg; CLI list + context.py _render_messages pass it. !task/!query labels exempt. Tests green (15/15 test_messages). Commit d3b0a92. Starting T2 (message lifecycle in consolidate.py).