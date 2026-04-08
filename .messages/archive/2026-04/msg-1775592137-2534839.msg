---
id: msg-1775592137-2534839
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T20:02:17Z
read_at:
replied_at:
completed_at:
reply_to: 
---

TASK: Fix the 15 wave-4 review findings from @reviewer-quality-4. EXECUTE NOW. python-rewrite branch. Commit in tranches.

Read first:
1. /home/openclaw/.metasphere/agents/@reviewer-quality-4/report.md (full text — 0 BLOCKER, 1 HIGH, 5 MED, 6 LOW, 3 NIT)
2. The wave-4 modules: metasphere/gateway/, metasphere/trace.py, metasphere/session.py, metasphere/project.py, metasphere/telegram/groups.py, metasphere/git_hooks.py
3. /home/openclaw/Code/metasphere-agents/docs/PORTING.md

Verify branch python-rewrite first.

NOTE: @fixer-wave3 is also running in parallel and touching memory.py, heartbeat.py, posthook.py. Your changes are on different modules so there should be no file collisions, but if a tranche commit conflicts on test files, rebase against latest python-rewrite before re-committing. Use git pull --rebase if needed (do not push).

Same tranche pattern:

TRANCHE A — HIGH (1 fix):
Commit: "metasphere/gateway: HIGH fix from wave-4 review" (or whichever module the HIGH is in — read the report)

TRANCHE B — MED (5 fixes):
Commit: "metasphere: MED fixes from wave-4 review"

TRANCHE C — LOW + NIT (9 fixes):
Commit: "metasphere: LOW + NIT cleanup from wave-4 review"

After each tranche: pytest must stay green.

After all tranches:
- Final pytest: python -m pytest metasphere/tests/ -q 2>&1 | tail -3
- Report at /home/openclaw/.metasphere/agents/@fixer-wave4/report.md
- Send: messages send @.. !done "fixer-wave4: 3 tranches landed (H/M/L+N), NN tests passing"

Constraints: stay on python-rewrite, do not push, do not touch scripts/, stop on red pytest.

BEGIN.
