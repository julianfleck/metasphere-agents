---
id: msg-1775530375-89422
from: @orchestrator
to: @orchestrator
label: !done
status: unread
scope: /
created: 2026-04-07T02:52:55Z
read_at:
replied_at:
completed_at:
reply_to: 
---

SPOT PATCH: metasphere-gateway fixed (atomic load-buffer/paste-buffer) installed to both /var/lib/machines/openclaw/home/openclaw/.metasphere/bin/metasphere-gateway and ~/Code/metasphere-agents/scripts/metasphere-gateway on spot. metasphere-telegram.service restarted, active/running. Telegram smoke test OK (message_id 17061 sent). IMPORTANT FINDINGS: (1) metasphere-telegram.service runs metasphere-telegram-stream which only writes latest.json — it does NOT inject into claude, so restarting it did not re-run the bug. (2) There is no metasphere-gateway systemd unit on spot; no metasphere-gateway process was found running. The openclaw-gateway.service is the unrelated Node OpenClaw gateway (currently failed, pre-existing). (3) metasphere-heartbeat ALSO has the line-shredding bug at lines 182-184 (loop of tmux send-keys $line Enter) and is a likely culprit for spot's killed claude session — same fix needed there. (4) Two claude processes still alive in container: pid 1344217 (original spot session, Mar31) and pid 1562997 (spawned child, 04:24). Did not touch them.
