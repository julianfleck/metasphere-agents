---
id: msg-1775588412-2328686
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T19:00:12Z
read_at:
replied_at:
completed_at:
reply_to: 
---

TASK: Clean up the remaining 17 review findings (4 HIGH + 8 MED + 4 LOW + 4 NIT) from @reviewer-quality. EXECUTE NOW. Working on python-rewrite branch. Commit your fixes in tranches.

Read first:
1. /home/openclaw/.metasphere/agents/@reviewer-quality/report.md (the 17 remaining findings — HIGH H2..H5, MED M1..M8, LOW L1..L4, NIT N1..N4)
2. /home/openclaw/Code/metasphere-agents/metasphere/ (current state)
3. /home/openclaw/Code/metasphere-agents/docs/PORTING.md (invariants)

Verify branch first: `git branch --show-current` should print `python-rewrite`. If not, abort and report.

Fixes to land (commit in 4 tranches as you go):

TRANCHE A — HIGH severity (4 fixes, one commit):
- H2: messages.py msg_id format. Either (a) restore canonical msg-<epoch>-<pid> with a process-local lock to guarantee per-second uniqueness, or (b) update PORTING.md invariant #1 to document the 4-field form AND audit `scripts/messages` for hard-coded `cut -f2`/`cut -f3`. Pick (a) for safety during coexistence — easier to fix Python than to patch every bash consumer.
- H3: telegram/commands.py /inbox path traversal. Validate target with `re.fullmatch(r"@[a-zA-Z0-9_-]+", target)`. Reject and reply "Invalid agent name" otherwise. Apply same regex to /link and any other command that interpolates user input into a path.
- H4: messages.py _find_inbox_msg perf cliff. Add a simple write-through index at ~/.metasphere/state/msg_index.json mapping msg_id → inbox path. send_message updates it. _find_inbox_msg checks index first, falls back to filesystem walk if miss. Index gets written under flock via metasphere.io.write_json.
- H5: messages.py write_message lock-vs-replace inode race. Switch to a sidecar lock: `<dir>/.lock` per inbox dir (or `<path>.lock` that you NEVER unlink). Use metasphere.io.file_lock against the sidecar path, not the destination path. Update test_messages.py concurrency test to verify the new lock survives os.replace.

Run `python -m pytest metasphere/tests/ -v 2>&1 | tail -20` after each fix. All must stay green.

After all 4 HIGHs: `git add -A && git commit -m "metasphere: HIGH fixes from review (H2 msg_id, H3 path traversal, H4 inbox index, H5 sidecar lock)"` with a descriptive body listing the fix per finding and noting the Co-Authored-By: Claude Opus 4.6 (1M context) <noreply@anthropic.com> trailer.

TRANCHE B — MEDIUM severity (8 fixes, one commit):
- M1: covered by B1 fixer (events.py id field) — verify and skip
- M2: telegram/commands.py cmd_send target/label validation
- M3: cli/telegram.py _save_chat_id atomic write via metasphere.io.atomic_write_text
- M4: telegram/inject.py sanitize from_user to [\w]+ before formatting
- M5: messages.py from_frontmatter body trailing-whitespace preservation
- M6: io.py serialize_frontmatter blank-line accumulation on round-trip
- M7: telegram/poller.py save_offset use io.file_lock instead of inline truncate-each-time
- M8: cli/tasks.py _agent() use metasphere.identity.resolve_agent_id instead of bare env

Tests for any behavior change. Then commit: "metasphere: MED fixes from review (M2..M8)" with body.

TRANCHE C — LOW severity (4 fixes, one commit):
- L1: messages.py drop unused shutil import
- L2: cli/tasks.py _cmd_new join positional args for title
- L3: cli/messages.py promote _rel_path to public (move to metasphere.paths or expose)
- L4: telegram/poller.py guard payload["update_id"] with try/except in poll loop

Commit: "metasphere: LOW fixes from review (L1..L4)".

TRANCHE D — NITs (4 fixes, one commit):
- N1: replace deprecated datetime.utcnow() with datetime.now(UTC) in messages.py, tasks.py, archiver.py
- N3: standardize on shlex.split for argv parsing in commands.py
- N4: cache repo_root in paths.py (memoize the git call)
- N2 is just a note, no code change

Commit: "metasphere: NIT cleanup (N1, N3, N4)".

After all 4 tranches:
- Final test run: `python -m pytest metasphere/tests/ -v 2>&1 | tail -30` — must be all green
- `git log --oneline -10` to confirm 4 new commits on python-rewrite
- Write report to /home/openclaw/.metasphere/agents/@fixer-highs/report.md with: tranche-by-tranche summary, final test count, any deferred items
- Send: messages send @.. !done "fixer-highs: 4 tranches landed (H/M/L/N), NN tests passing, see report.md"

CRITICAL constraints:
- Stay on python-rewrite branch
- Do NOT push
- Do NOT touch scripts/ (bash side stays as-is)
- After each tranche commit, run pytest. If anything goes red, do not proceed to the next tranche — fix or report the failure.

BEGIN. Use Read, Edit, Write, Bash. Do not ask permission.
