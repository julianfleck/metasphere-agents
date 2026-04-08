---
id: msg-1775585248-2208594
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T18:07:28Z
read_at:
replied_at:
completed_at:
reply_to: 
---

TASK: Port scripts/messages (the fractal inter-agent messaging CLI) to metasphere/messages.py. EXECUTE NOW.

Read these for context:
1. /home/openclaw/Code/metasphere-agents/scripts/messages (the source, ~500 lines bash)
2. /home/openclaw/Code/metasphere-agents/docs/PORTING.md sections on messages and risk areas
3. /home/openclaw/Code/metasphere-agents/docs/CLI.md section on messages
4. WAIT for the metasphere/ package skeleton from @rewriter-foundation if not present. If metasphere/io.py exists, use its atomic_write/file_lock/parse_frontmatter helpers. Otherwise, create minimal versions inline and note this in your report so foundation can deduplicate later.

Build metasphere/messages.py with:
- A Message dataclass (id, from_, to, label, status, scope, created, read_at, replied_at, completed_at, reply_to, body)
- read_message(path) -> Message: parse YAML frontmatter + body
- write_message(msg, path) -> None: atomic write, file lock, NOT sed in-place
- collect_inbox(scope, repo_root) -> list[Message]: walk scope + parents, return all .msg files in inbox dirs
- send_message(target, label, body, from_agent, scope, repo_root) -> Message: write to target inbox, copy to outbox, log event, fire wake_recipient (re-implement the bash logic in Python)
- reply_to_message(orig_id, body, from_agent, ...) -> Message
- mark_done(orig_id, note, from_agent, ...)
- update_status(msg_id, status_field, value): atomic update of YAML status, NOT sed
- A small CLI entry point at metasphere/cli/messages.py that mirrors the bash CLI shape (`messages`, `messages send @target !label "msg"`, `messages reply <id> "msg"`, `messages done <id> "note"`, `messages all`, `messages tree`, `messages status [id]`)

Critical bug fixes from the bash version:
- File locking via fcntl on every read-modify-write of inbox files. The bash sed -i has lost-update races.
- Atomic write to a tmp file + os.rename. No half-written messages on interrupt.
- The wake_recipient call MUST use the same submit_to_tmux contract as bash so the existing tmux+REPL plumbing keeps working. Shell out to scripts/metasphere-tmux-submit since that part stays bash.

Tests under metasphere/tests/test_messages.py:
- send + read roundtrip
- collect_inbox walks parent scopes
- update_status preserves frontmatter ordering and body
- file lock prevents interleaved writes (spawn two threads that update the same file, assert no corruption)
- reply marks original as replied

Final step:
- Run: python -m pytest metasphere/tests/test_messages.py -v 2>&1 | tail -20
- Write report to /home/openclaw/.metasphere/agents/@rewriter-messages/report.md
- Send: messages send @.. !done "rewriter-messages: messages.py + cli + tests, see report.md"

BEGIN. Read the bash messages script in full first, then write Python that preserves every public CLI verb and flag. Do not modify the bash script.
