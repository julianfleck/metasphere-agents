---
id: msg-1775591862-2508113
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T19:57:42Z
read_at:
replied_at:
completed_at:
reply_to: 
---

TASK: Fix the 17 wave-3 review findings from @reviewer-quality-3. EXECUTE NOW. python-rewrite branch. Commit in tranches.

Read first:
1. /home/openclaw/.metasphere/agents/@reviewer-quality-3/report.md (full text — 0 BLOCKER, 2 HIGH cutover-drift, 6 MED, 5 LOW, 4 NIT)
2. /home/openclaw/Code/metasphere-agents/metasphere/memory/ + heartbeat.py + posthook.py
3. /home/openclaw/Code/metasphere-agents/docs/PORTING.md

Verify branch python-rewrite first.

Same tranche pattern as fixer-wave2:

TRANCHE A — HIGH (2 fixes, one commit):
- Apply both HIGHs per the report. The reviewer marked them "cutover-drift" so the fixes align Python output byte-for-byte with bash output before the systemd unit / hook flips.
Commit: "metasphere: HIGH cutover-drift fixes from wave-3 review"

TRANCHE B — MED (6 fixes):
Commit: "metasphere: MED fixes from wave-3 review"

TRANCHE C — LOW + NIT (9 fixes):
Commit: "metasphere: LOW + NIT cleanup from wave-3 review"

After each tranche: pytest must stay green. Run python -m pytest metasphere/tests/ -q 2>&1 | tail -3 and confirm.

After all tranches:
- Final pytest
- Report at /home/openclaw/.metasphere/agents/@fixer-wave3/report.md
- Send: messages send @.. !done "fixer-wave3: 3 tranches landed (H/M/L+N), NN tests passing, see report.md"

Constraints: stay on python-rewrite, do not push, do not touch scripts/, stop on red pytest and report.

BEGIN.
