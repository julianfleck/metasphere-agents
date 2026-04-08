---
id: msg-1775568588-1796993
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T13:29:48Z
read_at:
replied_at:
completed_at:
reply_to: 
---

TASK: Fix telegram bot slash commands. EXECUTE THESE STEPS NOW USING BASH AND OTHER TOOLS:
1. Read scripts/metasphere-telegram lines 126 to 323 to enumerate all slash commands actually implemented (the case dispatch). Build the list.
2. Source ~/.metasphere/config/telegram.env to get TELEGRAM_BOT_TOKEN.
3. Call getMyCommands via curl to see what is currently registered with BotFather.
4. Build a JSON commands array matching what is implemented in metasphere-telegram (command + short description for each).
5. Call setMyCommands via curl to register the metasphere set, replacing whatever stale openclaw set is there.
6. Call getMyCommands again to verify.
7. Write a report to /home/openclaw/.metasphere/agents/@slash-fixer/report.md with: before-set, after-set, diff, any errors.
8. Send completion via: messages send @.. !done "slash-fixer report at /home/openclaw/.metasphere/agents/@slash-fixer/report.md - registered N commands"
BEGIN NOW. Use Bash, Read, Write tools. Do not ask for permission.
