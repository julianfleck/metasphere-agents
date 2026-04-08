---
id: msg-1775585937-2244198
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T18:18:57Z
read_at:
replied_at:
completed_at:
reply_to: 
---

TASK: Code quality + security review of the metasphere/ Python rewrite. EXECUTE NOW.

Read first:
1. /home/openclaw/Code/metasphere-agents/docs/PORTING.md (the contract — risk areas + invariants)
2. /home/openclaw/Code/metasphere-agents/docs/CLI.md (semantic compatibility target)
3. Every file under /home/openclaw/Code/metasphere-agents/metasphere/ (foundation + tasks + telegram + messages)
4. Both reports: ~/.metasphere/agents/@rewriter-{foundation,tasks,telegram,messages}/report.md

Review across these axes:
- **Correctness:** does each module preserve the bash CLI contract? Any missing verbs/flags? Any silent semantic drift?
- **Concurrency:** every read-modify-write of a shared file MUST be under fcntl.flock. Any path that mutates without a lock is a bug. Check messages, tasks, telegram offset, events log.
- **Atomicity:** every write to shared state MUST go through tmp+rename. Any direct open(path, "w") on shared state is a bug.
- **Security:** any subprocess call that takes user-controlled input via shell=True or unsplit string. Any path traversal risk in resolve_target or slug sanitization. Any token leak in logs.
- **Type safety:** missing type annotations, broken Optional handling, dataclass field ordering vs frontmatter serialization.
- **Test coverage gaps:** what edge cases are NOT tested? Specifically per-PORTING invariants 7/8/15/16.
- **Code smells:** duplication between modules (io vs inlined helpers), naming inconsistencies, dead code, TODO comments.
- **Stdlib-only constraint** honored?
- **No SQLite** anywhere — confirmed?
- **Dead code from bash that should NOT have been ported** (cmd_view, cmd_subtree, etc per PORTING dead-code list)

For each finding: severity (BLOCKER / HIGH / MEDIUM / LOW / NIT), file:line, description, suggested fix. Group by severity in the report.

Also score each module 1-10 on: correctness, concurrency-safety, security, code-quality, test-coverage. Average = module score.

Final step:
- Run python -m pytest metasphere/tests/ -v 2>&1 | tail -30 to confirm baseline
- Run shellcheck on scripts/metasphere-tmux-submit (the bash dependency that messages.py shells out to) to verify it has no smells the rewrite is implicitly trusting
- Write report to /home/openclaw/.metasphere/agents/@reviewer-quality/report.md
- Send: messages send @.. !done "reviewer-quality: full review with N findings (BLOCKER/HIGH/MED/LOW), see report.md"

BEGIN. Read-only: do NOT modify any files. Just report findings. Use Read, Bash, Glob, Grep.
