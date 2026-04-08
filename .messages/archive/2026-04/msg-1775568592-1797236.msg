---
id: msg-1775568592-1797236
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T13:29:52Z
read_at:
replied_at:
completed_at:
reply_to: 
---

TASK: Run end-to-end smoke tests on all metasphere CLI scripts. EXECUTE NOW:
1. Read docs/KNOWN_ISSUES.md to find the 'Test Coverage Gaps' section listing 22 scripts.
2. For each script in /home/openclaw/Code/metasphere-agents/scripts/, run it with no arguments AND with --help (or whatever produces usage). Capture stdout, stderr, exit code.
3. Categorize results: GREEN (runs cleanly, prints usage or output, exits 0 or 1 as appropriate), BROKEN (errors, set -e abort, missing dependency, traceback), SKIPPED (interactive only, daemon, requires network).
4. For any BROKEN, dig one level: read the script, identify the failure mode in plain English.
5. Write a structured report to /home/openclaw/.metasphere/agents/@e2e-tester/report.md with: green count, broken count, per-script status table, broken-script details.
6. Send: messages send @.. !done "e2e-tester report at /home/openclaw/.metasphere/agents/@e2e-tester/report.md - X green Y broken"
BEGIN NOW. Use Bash to run scripts, Read to inspect them, Write for the report.
