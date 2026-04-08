---
id: msg-1775585641-2230996
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T18:14:01Z
read_at:
replied_at:
completed_at:
reply_to: 
---

TASK: Port scripts/messages (the bash fractal messaging CLI) to metasphere/messages.py. EXECUTE NOW. The metasphere/ package skeleton already exists from @rewriter-foundation.

Read first:
1. /home/openclaw/Code/metasphere-agents/scripts/messages (~500 lines bash)
2. /home/openclaw/Code/metasphere-agents/metasphere/io.py — use atomic_write_text, file_lock, parse_frontmatter, serialize_frontmatter, read_frontmatter_file, write_frontmatter_file, Frontmatter
3. /home/openclaw/Code/metasphere-agents/metasphere/paths.py — use Paths.resolve()
4. /home/openclaw/Code/metasphere-agents/metasphere/events.py — use log_event
5. /home/openclaw/Code/metasphere-agents/metasphere/identity.py — use resolve_agent_id
6. /home/openclaw/Code/metasphere-agents/docs/PORTING.md messages section + risk #3 (file races)
7. Glance at /home/openclaw/Code/metasphere-agents/metasphere/tasks.py for the pattern (sibling rewriter-tasks already shipped)

Build /home/openclaw/Code/metasphere-agents/metasphere/messages.py with:
- Message dataclass: id, from_, to, label, status, scope, created, read_at, replied_at, completed_at, reply_to, body
- read_message(path) -> Message: parse via io.read_frontmatter_file
- write_message(msg, path): via io.write_frontmatter_file (atomic + locked)
- collect_inbox(scope, repo_root) -> list[Message]: walk scope and parents, find all .messages/inbox/*.msg
- send_message(target, label, body, from_agent, paths) -> Message: write to target inbox + outbox, log event, fire wake_recipient
- reply_to_message(orig_id, body, from_agent, paths)
- mark_done(orig_id, note, from_agent, paths)
- update_status(msg_path, field, value): atomic update under file_lock
- resolve_target(target_str, scope, repo_root) -> Path: handle @., @.., @/path/, @name
- wake_recipient_if_live(target, label, from_agent, body): subprocess to scripts/metasphere-tmux-submit (the tmux plumbing stays bash)

Build /home/openclaw/Code/metasphere-agents/metasphere/cli/messages.py mirroring:
  messages                        # cmd_inbox (unread only)
  messages all                    # cmd_inbox with all
  messages send @target !label "msg"
  messages reply <id> "msg"
  messages done <id> "note"
  messages read <id>
  messages tree
  messages status [id]

Tests at /home/openclaw/Code/metasphere-agents/metasphere/tests/test_messages.py (use the conftest tmp_paths fixture):
- send + read roundtrip
- collect_inbox walks parent scopes
- update_status preserves frontmatter ordering and body (no lost fields)
- file lock prevents interleaved writes (spawn 2 threads/processes that update same file, assert single coherent state)
- reply marks original as replied AND creates reply with reply_to set

CRITICAL: file locking on every read-modify-write (the bash sed -i has lost-update races). Atomic write to tmp + os.replace, no half-written messages on interrupt.

Final step:
- Run: python -m pytest metasphere/tests/test_messages.py -v 2>&1 | tail -25
- Write report to /home/openclaw/.metasphere/agents/@rewriter-messages/report.md with: files created, test results, decisions
- Send: messages send @.. !done "rewriter-messages: messages.py + cli + tests, see report.md"

BEGIN. Use Read, Write, Bash, Glob, Grep. Do not modify scripts/ or other metasphere/ files. Only write your three target files.
