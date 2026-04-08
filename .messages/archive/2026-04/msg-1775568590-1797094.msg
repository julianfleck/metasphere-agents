---
id: msg-1775568590-1797094
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T13:29:50Z
read_at:
replied_at:
completed_at:
reply_to: 
---

TASK: Test project-specific telegram groups setup end to end. EXECUTE NOW:
1. Read scripts/metasphere-telegram-groups in full to understand its API.
2. Read scripts/metasphere-project in full (if it exists).
3. Run: ~/.metasphere/bin/metasphere-telegram-groups list 2>&1 - capture output
4. Run: ~/.metasphere/bin/metasphere-telegram-groups (no args) - capture usage
5. Check if a forum supergroup is configured by looking for ~/.metasphere/config/telegram_forum_id
6. If configured: try to create a test topic named 'metasphere-e2e-test' via the create subcommand, verify it appears in list, then document the flow.
7. If NOT configured: document what is missing and what needs to happen (steps for the user to set up a forum supergroup with the bot).
8. Read scripts/metasphere-telegram lines for /groups and /link handlers to confirm the bot side works.
9. Write detailed report to /home/openclaw/.metasphere/agents/@groups-tester/report.md
10. Send: messages send @.. !done "groups-tester report at /home/openclaw/.metasphere/agents/@groups-tester/report.md"
BEGIN NOW. Use tools. Do not ask permission.
