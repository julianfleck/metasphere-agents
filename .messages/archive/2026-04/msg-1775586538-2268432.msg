---
id: msg-1775586538-2268432
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T18:28:58Z
read_at:
replied_at:
completed_at:
reply_to: 
---

TASK: Fix the 3 BLOCKER findings + H1 from @reviewer-quality. EXECUTE NOW.

Read first:
1. /home/openclaw/.metasphere/agents/@reviewer-quality/report.md (especially BLOCKERs B1, B2, B3, and HIGH H1)
2. /home/openclaw/Code/metasphere-agents/metasphere/events.py
3. /home/openclaw/Code/metasphere-agents/metasphere/tasks.py
4. /home/openclaw/Code/metasphere-agents/metasphere/io.py
5. /home/openclaw/Code/metasphere-agents/docs/PORTING.md (the invariants)

Fixes to make:

B1 — events.py: rewrite log_event to emit {id, timestamp, type, message, agent, scope, meta} per line.
- id: f"evt-{int(time.time()*1000)}-{os.getpid()}-{random_4hex}"
- timestamp: ISO-8601 string with Z suffix (date.now(UTC).strftime)
- agent: from arg or resolve_agent_id() fallback
- scope: from arg or paths.scope (relative to repo_root if possible)
- meta: dict, default {}
Update metasphere/tests/test_events.py to assert the new schema. Add a test that round-trips through jq if jq is available, else skip.

B2 — tasks.py: add created_by field to the Task dataclass + to_text + from_text + create_task. Default to resolve_agent_id() in create_task. Drop slug from the frontmatter (keep on the dataclass as a runtime-only field via field(repr=False, compare=False, default=None) or compute on demand from id). Update test_tasks.py to assert created_by exists in the written file.

B3 + H1 — tasks.py: rip out _atomic_write, _file_lock, _parse_frontmatter, _dump_frontmatter from tasks.py. Import from metasphere.io instead: atomic_write_text, file_lock, parse_frontmatter, serialize_frontmatter, read_frontmatter_file, write_frontmatter_file. The whole point: io.py is the single source of truth.

After fixes:
- Run python -m pytest metasphere/tests/ -v 2>&1 | tail -40 — must be all passing
- Run a small jq sanity check on the events log:
  python -c "from metasphere.events import log_event; from metasphere.paths import resolve; p=resolve(); log_event(\"test.fixer\", \"B1 fix verification\")"
  jq -r ".timestamp, .id, .type" /home/openclaw/.metasphere/events/events.jsonl | tail -3
  All three should be non-null.
- Write a delta report to /home/openclaw/.metasphere/agents/@fixer-blockers/report.md listing what was fixed and the test diff
- Send: messages send @.. !done "fixer-blockers: B1+B2+B3+H1 fixed, NN tests passing"

BEGIN. Edit the existing files (events.py, tasks.py, test_events.py, test_tasks.py). Do NOT touch other modules. Use Read, Edit, Bash.
