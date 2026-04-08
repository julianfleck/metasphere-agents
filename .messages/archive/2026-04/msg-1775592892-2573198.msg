---
id: msg-1775592892-2573198
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T20:14:52Z
read_at:
replied_at:
completed_at:
reply_to: 
---

TASK: Final review of the python-rewrite branch as a whole. EXECUTE NOW. Read-only.

Read first:
1. /home/openclaw/Code/metasphere-agents/docs/PORTING.md (the contract)
2. /home/openclaw/Code/metasphere-agents/docs/CLI.md
3. /home/openclaw/Code/metasphere-agents/metasphere/ (whole package)
4. The 4 prior review reports: /home/openclaw/.metasphere/agents/@reviewer-quality{,-2,-3,-4}/report.md
5. The 4 prior fixer reports: /home/openclaw/.metasphere/agents/@fixer-{blockers,highs,wave2,wave3,wave4}/report.md
6. git log --oneline main..python-rewrite (32 commits)

Run python -m pytest metasphere/tests/ -v 2>&1 | tail -30 to confirm 158 passing baseline.

This is the FINAL gate before cutover. Verify:

A) **Every prior review finding (across the 4 review waves) is actually fixed** — open each report, walk through the findings list, verify each one against current code on python-rewrite. Build a pass/fail table.

B) **Cross-module invariants are respected**:
- Invariant 1 (msg id format): canonical msg-<epoch>-<pid> after wave-1 H2 fix
- Invariant 2 (task frontmatter): includes created_by after wave-1 B2 fix
- Invariant 6 (events schema): {id, timestamp, type, message, agent, scope, meta} per line after wave-1 B1 fix
- Invariant 7 (tmux session naming): metasphere-<name> across all consumers
- Invariant 8 (harness hash): bytewise matches the bash recipe
- Invariant 15 (tmux paste-submit): metasphere/* shells out to scripts/metasphere-tmux-submit
- Invariant 16 (wake protocol): @.. wake skipped when from_agent==@user

C) **Bash↔python interop**:
- A bash-emitted .msg file can be read by metasphere.messages.read_message and round-tripped
- A python-emitted .msg file can be read by `messages` bash CLI (run actual bash CLI as subprocess on a python-written file; assert output matches)
- A python-written events.jsonl line is parseable by `jq -r .timestamp,.id,.type,.scope`
- A python-written task is parseable by `tasks` bash CLI

D) **Cutover readiness**:
- Every script in scripts/ has a corresponding metasphere/ module (or is documented as "stays bash" in PORTING.md)
- The CLI shims under metasphere/cli/ cover every public verb
- The systemd-unit ExecStart lines that need flipping are documented in your report
- Identify any gaps that would block flipping the bash bot off

E) **Code quality holistic**:
- Average module score across the entire package
- Best and worst modules
- Any leftover technical debt that should be tracked as known issues but not blockers

F) **Test coverage holistic**:
- Number of tests per module
- Untested public functions (if any)
- Integration vs unit ratio

VERDICT — pick one:
- "READY FOR CUTOVER" — all invariants honored, interop verified, no blockers. The python rewrite can replace the bash implementation immediately by flipping symlinks + systemd units.
- "READY WITH KNOWN GAPS" — list the gaps that should be tracked but do not block cutover.
- "NOT READY" — list the must-fix items.

Final step:
- Write report at /home/openclaw/.metasphere/agents/@reviewer-final/report.md with the verdict prominent at the top
- Send: messages send @.. !done "reviewer-final: VERDICT (X gaps if any), see report.md"

BEGIN. Read-only.
