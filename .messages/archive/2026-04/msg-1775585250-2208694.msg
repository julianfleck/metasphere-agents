---
id: msg-1775585250-2208694
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T18:07:30Z
read_at:
replied_at:
completed_at:
reply_to: 
---

TASK: Port scripts/tasks to metasphere/tasks.py. EXECUTE NOW.

Read:
1. /home/openclaw/Code/metasphere-agents/scripts/tasks (source)
2. /home/openclaw/Code/metasphere-agents/docs/PORTING.md tasks section
3. /home/openclaw/Code/metasphere-agents/docs/CLI.md tasks section
4. WAIT for or use whatever metasphere/io.py provides (atomic_write, file_lock, parse_frontmatter)

Build metasphere/tasks.py with:
- A Task dataclass (id, title, priority, status, scope, created, started, completed, assignee, body, slug)
- create_task(title, priority, scope, repo_root) -> Task: writes to .tasks/active/<slug>.md with YAML frontmatter
- update_task(task_id, **fields): atomic update
- start_task(task_id, agent): assigns and marks in-progress
- complete_task(task_id, summary, repo_root): moves file from active/ to completed/
- list_tasks(scope, repo_root, include_completed=False) -> list[Task]
- Slug sanitization: replace / with - (the existing bash bug — fix for real). Also lowercase, strip non-alnum, max 60 chars.
- A CLI shim metasphere/cli/tasks.py mirroring `tasks`, `tasks new "title" !priority`, `tasks start <id>`, `tasks update <id> "note"`, `tasks done <id> "summary"`, `tasks list`, `tasks all`

Bug fixes from the bash version:
- Slash sanitization in slugs (KNOWN_ISSUES task low: "Fix tasks slug sanitization for slash chars")
- Atomic file moves from active/ to completed/ (bash mv is fine but use shutil.move for clarity)
- File locking on the source file when updating

Tests under metasphere/tests/test_tasks.py:
- create + read roundtrip
- slug sanitization including slashes
- update preserves frontmatter
- complete_task moves file to completed/
- list_tasks finds tasks across nested scopes

Final step:
- Run: python -m pytest metasphere/tests/test_tasks.py -v 2>&1 | tail -20
- Write report to /home/openclaw/.metasphere/agents/@rewriter-tasks/report.md
- Send: messages send @.. !done "rewriter-tasks: tasks.py + cli + tests, see report.md"

BEGIN. Do not modify scripts/tasks. Only write new files under metasphere/.
