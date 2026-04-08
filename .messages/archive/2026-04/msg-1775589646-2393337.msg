---
id: msg-1775589646-2393337
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T19:20:46Z
read_at:
replied_at:
completed_at:
reply_to: 
---

TASK: Review the 3 new modules from wave 2 (schedule, context, agents) against docs/PORTING.md invariants. EXECUTE NOW. Read-only.

Read first:
1. /home/openclaw/Code/metasphere-agents/docs/PORTING.md (focus on schedule/context/spawn sections + invariants 7,8,15,16)
2. /home/openclaw/Code/metasphere-agents/metasphere/schedule.py + cli/schedule.py + tests/test_schedule.py
3. /home/openclaw/Code/metasphere-agents/metasphere/context.py + cli/context.py + tests/test_context.py
4. /home/openclaw/Code/metasphere-agents/metasphere/agents.py + cli/agents.py + tests/test_agents.py
5. The recent commits: git show edc7088 64f0049 38897b3
6. /home/openclaw/.metasphere/agents/@reviewer-quality/report.md (the wave-1 review style — match its rigor)

Run python -m pytest metasphere/tests/ -v 2>&1 | tail -30 to confirm baseline 83 passing.

Score the 3 new modules + their CLI shims + their tests on the same 5 axes as the wave-1 review (correctness, concurrency, security, code-quality, test-coverage, 1-10 each, average = module score).

Findings categories same as wave 1: BLOCKER / HIGH / MED / LOW / NIT. For each finding: file:line, description, suggested fix.

Specific things to check:

schedule.py:
- No eval anywhere (PORTING risk #2)
- File lock on every read-modify-write of jobs.json
- Shrink-detection guard works (refuses zero-write when input had jobs)
- croniter.get_prev correctly converted to epoch and compared with 180s window
- Role-prefix dispatch (research-monitor:* → @research-*, polymarket:* → @polymarket, etc) matches the bash patch in scripts/metasphere-schedule cmd_run
- Atomic writes via metasphere.io
- Proper subprocess handling (no shell=True)

context.py:
- Harness hash byte-for-byte matches the bash recipe (sort filenames + xargs cat + sha256). Can verify by running both and comparing.
- All 7 sections emitted in canonical order
- truncate_section budgets honored
- Drift warning emitted when baseline differs from live
- No state mutation (read-only context build)

agents.py:
- spawn_ephemeral: matches scripts/metasphere-spawn behavior, METASPHERE_SPAWN_NO_EXEC=1 honored, log event
- wake_persistent: cold-start ready-poll for "bypass permissions" (invariant 15), Ctrl-U cleanup, alive-session inject path
- session_name_for matches "metasphere-<name>" exact (invariant 7)
- Subprocess to scripts/metasphere-tmux-submit (NOT reimplementing tmux paste race)
- File locking on agent metadata writes
- gc_dormant logic correct

Cross-cutting:
- Stdlib only (croniter is allowed since the bash version requires it; verify it is the only third-party import)
- No SQLite
- No silent semantic drift vs the bash version
- All public functions type-annotated
- Tests cover the BLOCKER class (the bash bugs we already know about)

Final step:
- Write report at /home/openclaw/.metasphere/agents/@reviewer-quality-2/report.md with: summary table (severity counts), per-module score table, finding-by-finding breakdown, and an explicit "READY TO MERGE TO MAIN" or "BLOCKERS PRESENT, NEEDS FIXER" verdict.
- Send: messages send @.. !done "reviewer-quality-2: NN findings (X BLOCKER, Y HIGH, ...), modules avg N.N/10, see report.md"

BEGIN. Read-only: do not modify any files. Use Read, Bash, Glob, Grep.
