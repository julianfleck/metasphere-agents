---
id: msg-1775585247-2208520
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T18:07:27Z
read_at:
replied_at:
completed_at:
reply_to: 
---

TASK: Build the metasphere/ Python package foundation. EXECUTE NOW.

Read these for context (in order):
1. /home/openclaw/Code/metasphere-agents/docs/PORTING.md (the audit doc, especially "rewrite priorities" and "what stays bash")
2. /home/openclaw/Code/metasphere-agents/docs/CLI.md sections on overall layout
3. /home/openclaw/.claude/projects/-home-openclaw-Code-metasphere-agents/memory/python_rewrite_directive.md (constraints: file-based no SQLite, parallel-track via separate bot, atomic writes + locks)

Then create the /home/openclaw/Code/metasphere-agents/metasphere/ Python package with these modules:

1. metasphere/__init__.py — version, package metadata
2. metasphere/paths.py — single source of truth for all metasphere paths (METASPHERE_DIR, REPO_ROOT, agents/, schedule/, state/, events/, telegram/, etc.). Resolve from env vars with same defaults as the bash. Returns pathlib.Path objects.
3. metasphere/io.py — atomic write helpers (write-to-tmp + rename), file locking via fcntl.flock, JSON read/write with locks, YAML frontmatter parser/serializer for the .msg/.md files (frontmatter dict + body string). Type-annotated.
4. metasphere/config.py — load env files (~/.metasphere/config/*.env), typed config dataclass for telegram tokens (BOTH the canonical and the rewrite token from telegram-rewrite.env), repo root, scope, agent id.
5. metasphere/identity.py — resolve current agent id from METASPHERE_AGENT_ID env or fall back to @user. Mirrors scripts/metasphere-identity but in Python.
6. metasphere/events.py — append to ~/.metasphere/events/events.jsonl with file lock. log_event(type, message, agent=None, meta={}).

Constraints:
- Python 3.11+. Type annotations on everything. dataclasses for structured records. pathlib for paths. NO third-party deps for now (stdlib only).
- File-based, no SQLite. Atomic writes everywhere. Locks on any file mutated by more than one process.
- Each module gets a docstring at the top explaining what it owns and what bash script it replaces.
- Tests under metasphere/tests/test_<module>.py using pytest. Each module gets a test file even if minimal — test atomic write, test lock, test paths resolution, test config load, test event append concurrency.

Final step:
- Run: python -m pytest metasphere/tests/ -x 2>&1 | tail -20
- Write a report to /home/openclaw/.metasphere/agents/@rewriter-foundation/report.md with: files created, test results, any decisions you made, blockers/notes for the other rewriter agents
- Send: messages send @.. !done "rewriter-foundation: package skeleton + 6 modules + tests, see report.md"

BEGIN. Use Read, Write, Bash, Glob, Grep. Do not ask permission. Do not modify any existing scripts/. Only write new files under metasphere/.
