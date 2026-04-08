---
id: msg-1775596650-2677797
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T21:17:30Z
read_at:
replied_at:
completed_at:
reply_to: 
---

Run a full end-to-end systems check on the freshly merged python harness on main (commit 166b3a8). Goal: verify every user-facing surface still works after the bash→python cutover, on real files in ~/.metasphere/ (not pytest fixtures).

Coverage targets — for each, report PASS/FAIL with the exact command you ran:

1. CLI smokes: metasphere-{messages,tasks,agents,heartbeat,schedule,context,posthook,telegram,trace,session,project,gateway,memory} all respond to --help without error.
2. Messages round-trip: send a !info from a fake @e2e-test agent to @orchestrator scope, list it, mark done, verify state file moved to read.
3. Tasks round-trip: create a !low task, start, update, done, verify file moved to completed/.
4. Schedule: list jobs, dry-run a cron fire, verify schedule.log gets an entry.
5. Heartbeat: trigger a one-shot heartbeat (NOT the daemon — call the CLI directly), verify it produces context output and does NOT inject anything to the orchestrator tmux for an idle tick.
6. Telegram: getme via metasphere-telegram getme; verify token resolution picks the canonical bot (not REWRITE).
7. Memory: metasphere-memory search 'cutover' returns hits from docs/ and agent reports.
8. Context hook: metasphere-context with no args produces the per-turn injection block on stdout, well-formed.
9. Posthook: pipe a fake stop-hook JSON payload to metasphere-posthook and verify it would route to telegram (--dry-run if available; otherwise inspect the parsed payload).
10. Trace: emit one trace event, verify it lands in ~/.metasphere/logs/trace.jsonl.
11. Gateway: metasphere-gateway --help and a status query.
12. Git hooks: metasphere-git-hooks --help, install dry-run.

DO NOT touch real production state — use a temp scope under /tmp/e2e-2677785/ where possible. If a CLI mutates real files, document what it touched.

Constraints:
- Read-only on ~/.metasphere/agents/@orchestrator/ and ~/.metasphere/messages/
- No telegram sends — use getMe-only paths
- Do NOT spawn child agents
- Do NOT commit anything
- Time budget: aggressive. Don't get stuck on one failure — log it and move on.

Output: a single ~/.metasphere/agents/@e2e-systems-check/report.md with a checklist (PASS/FAIL/SKIP per item), the failures grouped at the top with exact reproduction commands, and a final verdict line: 'green' / 'yellow (N non-blocking)' / 'red (N blocking)'.

When done: messages send @.. !done with the verdict line.
