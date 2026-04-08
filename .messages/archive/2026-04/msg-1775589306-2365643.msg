---
id: msg-1775589306-2365643
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T19:15:06Z
read_at:
replied_at:
completed_at:
reply_to: 
---

TASK: Port scripts/metasphere-context (the precommand hook that injects messages+tasks+CAM+telegram into every turn) to metasphere/context.py + cli. EXECUTE NOW. On python-rewrite branch. Commit your work.

Read first:
1. /home/openclaw/Code/metasphere-agents/scripts/metasphere-context (the source — read in full, ~250 lines)
2. /home/openclaw/Code/metasphere-agents/docs/PORTING.md (context section + invariants 7,8,15,16)
3. /home/openclaw/Code/metasphere-agents/metasphere/messages.py (use collect_inbox)
4. /home/openclaw/Code/metasphere-agents/metasphere/tasks.py (use list_tasks)
5. /home/openclaw/Code/metasphere-agents/metasphere/events.py (read recent events)
6. /home/openclaw/Code/metasphere-agents/metasphere/paths.py

Verify branch python-rewrite first. Abort if not.

Build /home/openclaw/Code/metasphere-agents/metasphere/context.py with:
- build_context(paths) → str: assembles the multi-section context block emitted on every turn
- Sections (ORDER MATTERS — match bash exactly so the orchestrator turn-context stays stable):
  1. Status header
  2. Harness drift warning (read state/harness_hash_baseline, compute live hash from CLAUDE.md + .claude/settings.local.json + .claude/settings.json + scripts/metasphere-context, compare, emit warning if drift)
  3. Telegram recent conversation (last 5 messages from telegram/stream/<today>.jsonl)
  4. Messages (collect_inbox, render with the same icon scheme: ○ unread, ◐ read, ◑ replied, ● completed)
  5. Tasks (list_tasks active, render priority + slug)
  6. Recent events (last 10 events from events.jsonl)
  7. Memory Context FTS (call out to scripts/metasphere-fts if it exists, else skip)
- truncate_section(text, budget=2048) → str: cap each section
- harness_hash(paths) → str: computes the same canonical hash as the bash version (sort filenames, cat, sha256)

Build /home/openclaw/Code/metasphere-agents/metasphere/cli/context.py: stdout-only emitter for the precommand hook. `metasphere-context` prints the assembled context, exit 0.

Critical:
- The bash version is what claude-code currently invokes per turn. The Python rewrite is a parallel implementation; do NOT replace the symlink in ~/.metasphere/bin yet — that is a cutover step for later.
- Honor invariant 7 (tmux session naming) and invariant 8 (harness hash file) byte-for-byte. The harness hash recipe is documented in scripts/metasphere-context lines 49-87.
- Use metasphere.io.read_json / write_json for any state file reads.
- No external deps; stdlib only.

Tests at /home/openclaw/Code/metasphere-agents/metasphere/tests/test_context.py:
- harness_hash matches the canonical bash recipe (compare against `cat ... | sort | xargs cat | sha256sum`)
- build_context emits all 7 sections in order
- drift warning emitted when baseline hash differs from live
- truncate_section caps long sections at the budget
- empty inbox/tasks/events do not crash

Final step:
- pytest the new test file + the full suite
- Commit: `git add metasphere/context.py metasphere/cli/context.py metasphere/tests/test_context.py && git commit -m "metasphere: port scripts/metasphere-context (harness drift detector + per-turn injection)"` with body
- Write report
- Send: messages send @.. !done "rewriter-context: context.py + cli + tests, NN passing, committed"

BEGIN.
