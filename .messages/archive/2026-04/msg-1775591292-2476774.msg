---
id: msg-1775591292-2476774
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T19:48:12Z
read_at:
replied_at:
completed_at:
reply_to: 
---

TASK: Review the 3 wave-3 modules (memory, heartbeat, posthook) against docs/PORTING.md. EXECUTE NOW. Read-only.

Read first:
1. /home/openclaw/Code/metasphere-agents/docs/PORTING.md
2. /home/openclaw/Code/metasphere-agents/metasphere/memory/ (full subpackage: __init__, base, fts, cam, hybrid, api)
3. /home/openclaw/Code/metasphere-agents/metasphere/heartbeat.py + cli/heartbeat.py + tests/test_heartbeat.py
4. /home/openclaw/Code/metasphere-agents/metasphere/posthook.py + cli/posthook.py + tests/test_posthook.py
5. /home/openclaw/Code/metasphere-agents/metasphere/cli/memory.py + tests/test_memory.py
6. The 3 commits: git show ae2517b e0e6256 7917f95
7. /home/openclaw/.metasphere/agents/@reviewer-quality-2/report.md (match the wave-2 review style)

Run python -m pytest metasphere/tests/ -v 2>&1 | tail -30 to confirm baseline 116 passing.

Score the new modules (5 axes 1-10 each, average per module). Findings categories: BLOCKER / HIGH / MED / LOW / NIT, with file:line, description, suggested fix.

Specific things to verify:

memory/:
- MemoryStrategy ABC is clean and the 3 strategies actually implement it
- TokenOverlapStrategy is pure Python (no rg/awk dependency), produces scores in [0..1]
- CamStrategy gracefully handles missing cam binary (returns empty list, no exception)
- HybridStrategy weights default to cam=0.6 fts=0.4, dedupes properly
- recall() and context_for() are the public API and work as documented
- context.py cutover to metasphere.memory.context_for is correct (no longer shells out to scripts/metasphere-fts)
- No state mutation in search() — strategies are read-only
- New strategies (embeddings, FTS5) could be plugged in without touching call sites — verify the abstraction holds

heartbeat.py:
- check_urgent_messages, check_blocked_agents, check_urgent_tasks behave like the bash version
- Dedupe state file works (calling twice on same urgent does not double-notify)
- build_agent_context prepends "# HEARTBEAT" and includes the standard sections
- invoke_agent_heartbeat picks tmux fast path when session_alive, else one-shot fallback via subprocess
- Mock subprocess in tests; no real claude calls in CI

posthook.py:
- read_stop_hook_payload + extract_last_assistant_text + should_skip_silent_tick + route_to_telegram + track_turn_completion all do what the bash version does
- DEDUPE via state hash file
- NEVER raises (always returns 0)
- Uses metasphere.telegram.api.send_message (single source of truth, parallel-track bot)
- File locking on activity.json
- No actual telegram calls in tests (mock the api)

Cross-cutting:
- Stdlib only
- No SQLite
- No silent semantic drift vs the bash version
- Tests adequate
- No metasphere_ prefixed identifiers (the naming cleanup convention from wave-2 fixer)

Final step:
- Write report at /home/openclaw/.metasphere/agents/@reviewer-quality-3/report.md with the same structure as wave-2: summary table, per-module score table, finding-by-finding breakdown, READY TO MERGE TO MAIN or NEEDS FIXER verdict.
- Send: messages send @.. !done "reviewer-quality-3: NN findings (X BLOCKER, Y HIGH, ...), wave-3 modules avg N.N/10, see report.md"

BEGIN. Read-only. Use Read, Bash, Glob, Grep.
