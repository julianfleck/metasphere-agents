---
id: msg-1775585253-2208918
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T18:07:33Z
read_at:
replied_at:
completed_at:
reply_to: 
---

TASK: Port scripts/metasphere-telegram + metasphere-telegram-stream into metasphere/telegram/. EXECUTE NOW.

Read:
1. /home/openclaw/Code/metasphere-agents/scripts/metasphere-telegram (slash command dispatch + send)
2. /home/openclaw/Code/metasphere-agents/scripts/metasphere-telegram-stream (poller + archiver + injector)
3. /home/openclaw/Code/metasphere-agents/docs/PORTING.md sections on telegram (risk #1: parse_mode silent failure exists in 4 places — kill the class)
4. /home/openclaw/.metasphere/config/telegram-rewrite.env (the parallel-track bot token: 8604616317:AAGyOrpVJf0V1aZqT7JzHxnf5yQ0t8n4Ke8)

Build metasphere/telegram/ as a subpackage:
- metasphere/telegram/__init__.py
- metasphere/telegram/api.py — single source-of-truth send_message(chat_id, text, parse_mode=None) function. Default parse_mode=None (plain text). Auto-chunks text > 3900 chars at paragraph/line boundaries with [part/total] markers. Returns the API response. Raises TelegramAPIError on .ok != true.
- metasphere/telegram/poller.py — getUpdates polling loop (replaces the bash-curl loop in metasphere-telegram-stream). Persists offset to ~/.metasphere/telegram/offset with file lock. Yields parsed Update dataclasses.
- metasphere/telegram/archiver.py — append messages to ~/.metasphere/telegram/stream/YYYY-MM-DD.jsonl with file lock. Save latest.json. Mirror what the bash script does.
- metasphere/telegram/commands.py — slash command dispatch. /status, /tasks, /agents, /messages, /inbox, /send, /cam, /groups, /link, /events, /tree, /spot, /ping, /help. Each command calls into other metasphere/* modules (e.g. /tasks calls metasphere.tasks.list_tasks).
- metasphere/telegram/inject.py — when a non-command text message arrives, call submit_to_tmux on the orchestrator session via subprocess to scripts/metasphere-tmux-submit. Same contract as the bash version.
- metasphere/cli/telegram.py — CLI entry: `telegram poll`, `telegram send "msg"`, `telegram once`

Use the REWRITE bot token (TELEGRAM_BOT_TOKEN_REWRITE from telegram-rewrite.env) NOT the canonical bot. The new system runs in parallel against @metabotmetabotmetabot. Add a clear comment explaining this.

Critical bug fixes:
- parse_mode default is None (plain text) everywhere. No silent Markdown parse failures. The single api.py send_message is the ONLY way to call sendMessage in the whole package.
- Auto-chunking on >3900 chars (Telegram cap is 4096)
- Atomic write to offset file with fcntl lock to prevent the lost-update race the bash version has
- Use --data-urlencode equivalent (urllib.parse.urlencode with quote_via=urllib.parse.quote)

Tests under metasphere/tests/test_telegram.py (mock the API):
- send_message strips parse_mode by default
- send_message chunks at 3900 char boundary
- send_message raises on API error
- offset persistence is atomic
- archive_message appends without lock contention

Final step:
- Run: python -m pytest metasphere/tests/test_telegram.py -v 2>&1 | tail -30
- Verify the new bot is reachable: python -c "import urllib.request, json; print(json.loads(urllib.request.urlopen(\"https://api.telegram.org/bot8604616317:AAGyOrpVJf0V1aZqT7JzHxnf5yQ0t8n4Ke8/getMe\").read())[\"result\"][\"username\"])"
- Write report to /home/openclaw/.metasphere/agents/@rewriter-telegram/report.md
- Send: messages send @.. !done "rewriter-telegram: telegram subpackage + cli + tests, see report.md"

BEGIN. Do not touch the existing bash telegram scripts. Only write new Python files under metasphere/telegram/.
