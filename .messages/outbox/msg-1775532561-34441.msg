---
id: msg-1775532561-34441
from: @orchestrator
to: @orchestrator
label: !done
status: unread
scope: /
created: 2026-04-07T03:29:21Z
read_at:
replied_at:
completed_at:
reply_to: 
---

CRITICAL FIXES: A=fixed (added 0.5s sleep between tmux paste-buffer and Enter in gateway/heartbeat/session - root cause was claude TUI bracketed-paste end sequence racing the Enter keystroke for large pastes, causing the [Pasted text #N] placeholder to sit unsubmitted; synced to ~/.metasphere/bin/) B=fixed (added -p 2323 to ssh data.basicbold.de in metasphere-telegram /spot command) C=healthy (1 launchd gateway daemon PID 15566 12min uptime 0.5s CPU, watchdog ~1/min not tight loop, logs ~200K total, no respawn loops; one stale ssh worldwire ww search subprocess from 7:21PM hung on remote - candidate to kill but not consuming resources; responsiveness drop was caused by Bug A not resources). Committed as 0965341.
