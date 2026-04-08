---
id: msg-1775591863-2508191
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T19:57:43Z
read_at:
replied_at:
completed_at:
reply_to: 
---

TASK: Review the new wave-4 modules: gateway, trace, session, project, telegram-groups, git-hooks. EXECUTE NOW. Read-only.

Read first:
1. /home/openclaw/Code/metasphere-agents/docs/PORTING.md
2. /home/openclaw/Code/metasphere-agents/metasphere/gateway/ (full subpackage: __init__, session, watchdog, daemon)
3. /home/openclaw/Code/metasphere-agents/metasphere/trace.py
4. /home/openclaw/Code/metasphere-agents/metasphere/session.py
5. /home/openclaw/Code/metasphere-agents/metasphere/project.py
6. /home/openclaw/Code/metasphere-agents/metasphere/telegram/groups.py
7. /home/openclaw/Code/metasphere-agents/metasphere/git_hooks.py
8. Their CLI shims and tests
9. The commits: git show 4d7a9e3 d3c9e5e b6fcd85 e33c6e7 ef56c07 1fb8e24
10. /home/openclaw/.metasphere/agents/@reviewer-quality-3/report.md (style reference)

Run python -m pytest metasphere/tests/ -v 2>&1 | tail -30 to confirm 155 passing baseline.

Score each module on 5 axes (correctness, concurrency, security, code-quality, test-coverage, 1-10 each).

Specific things to verify per module:

gateway/:
- run_daemon does NOT have the bash daemon-flap bug (single iteration error must NOT exit the daemon)
- session lifecycle management is clean
- watchdog detects stuck-paste + safety-hooks confirmation
- Shells out to scripts/metasphere-tmux-submit (does NOT reimplement paste race per invariant 15)
- Uses metasphere.telegram.poller (does NOT reimplement getUpdates)

trace.py:
- Subprocess output capture is safe (no shell=True)
- index.jsonl is appended under flock
- search_traces is bounded (no full repo walk)
- prune_traces removes only old files

session.py:
- tmux helpers are read-only against the live system
- Does not duplicate metasphere.agents.session_alive

project.py:
- changelog actually writes (the bash version was broken — verify the fix)
- learnings flag is correct (the bash version had inverted flag — verify)

telegram/groups.py:
- Forum topic creation via the parallel-track bot
- topics.json under flock
- DROPS the bash dead-code branches (process_forum_command was unreachable)
- send_message uses metasphere.telegram.api (single source of truth)

git_hooks.py:
- install_hooks drops shell shims that exec python -m metasphere.cli.git_hooks
- uninstall_hooks reverses cleanly
- The hook bodies are tiny — actual logic is in Python

Cross-cutting:
- Stdlib only
- No SQLite
- No metasphere_ prefixed identifiers
- No silent semantic drift vs bash

Final step:
- Write report at /home/openclaw/.metasphere/agents/@reviewer-quality-4/report.md
- Send: messages send @.. !done "reviewer-quality-4: NN findings (X BLOCKER, Y HIGH, ...), wave-4 modules avg N.N/10, see report.md"

BEGIN. Read-only.
