---
id: msg-1775591297-2477884
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T19:48:17Z
read_at:
replied_at:
completed_at:
reply_to: 
---

TASK: Port the remaining small bash scripts in one shot: trace, session, project, telegram-groups, git-hooks. EXECUTE NOW. python-rewrite branch. Commit.

These are the tail of the bash inventory — small, well-isolated modules. Group them into commits per module.

Read first:
1. /home/openclaw/Code/metasphere-agents/scripts/metasphere-trace (~280 lines — capture command outputs to ~/.metasphere/traces/)
2. /home/openclaw/Code/metasphere-agents/scripts/metasphere-session (~270 lines — tmux session helpers)
3. /home/openclaw/Code/metasphere-agents/scripts/metasphere-project (~360 lines — project init + changelog)
4. /home/openclaw/Code/metasphere-agents/scripts/metasphere-telegram-groups (~340 lines — forum topic management)
5. /home/openclaw/Code/metasphere-agents/scripts/metasphere-git-hooks (~280 lines — install pre-commit / post-commit hooks)
6. /home/openclaw/Code/metasphere-agents/docs/PORTING.md sections on each
7. /home/openclaw/Code/metasphere-agents/metasphere/io.py + paths.py + events.py (use these)

Verify branch python-rewrite first.

Build (one module at a time, commit per module):

1. metasphere/trace.py + cli/trace.py + tests/test_trace.py
   - Trace dataclass: id, timestamp, agent, scope, command, exit_code, duration_ms, stdout_file, stderr_file, error_detected, error_type, error_summary
   - capture_trace(command_argv, agent, scope) → Trace: subprocess.run with capture, write stdout/stderr/json to ~/.metasphere/traces/YYYY-MM-DD/, append to index.jsonl under flock
   - search_traces(pattern, since, agent) → list[Trace]
   - prune_traces(older_than_days)
   - CLI: metasphere trace capture <argv...>, metasphere trace list, metasphere trace search <pattern>, metasphere trace prune
   - Commit: "metasphere/trace: port scripts/metasphere-trace (command output capture)"

2. metasphere/session.py + cli/session.py + tests/test_session.py
   - tmux session management helpers separate from gateway. list_sessions() → list[str], session_info(name) → SessionInfo, attach_to(name) → exec tmux attach
   - Use metasphere.agents.session_alive / session_name_for where applicable
   - CLI: metasphere session list, metasphere session info <name>, metasphere session attach <name>
   - Commit: "metasphere/session: port scripts/metasphere-session"

3. metasphere/project.py + cli/project.py + tests/test_project.py
   - Project dataclass: name, path, scope
   - init_project(name, path), list_projects(), project_changelog(path), project_learnings(path)
   - The bash cmd_changelog is documented as broken in KNOWN_ISSUES (writes nothing). Skip that path or fix it cleanly here.
   - The bash cmd_learnings has an inverted flag bug. Fix it here.
   - CLI: metasphere project init, metasphere project list, metasphere project changelog, metasphere project learnings
   - Commit: "metasphere/project: port scripts/metasphere-project (fixes broken changelog + inverted learnings flag)"

4. metasphere/telegram/groups.py + cli/telegram_groups.py + tests/test_telegram_groups.py
   - Forum topic management: create_topic(name), list_topics(), workspace(kind, name)
   - Subprocess to telegram bot API for createForumTopic / getForumTopicIconStickers etc.
   - Persist topic ids to ~/.metasphere/telegram/topics.json under flock
   - DROP the bash dead-code branches: process_forum_command was unreachable per PORTING audit
   - Commit: "metasphere/telegram/groups: port scripts/metasphere-telegram-groups"

5. metasphere/git_hooks.py + cli/git_hooks.py + tests/test_git_hooks.py
   - install_hooks(repo_path): drops pre-commit + post-commit shims into .git/hooks/ that call back into metasphere
   - uninstall_hooks(repo_path)
   - The hook bodies should be tiny shell shims that exec `python -m metasphere.cli.git_hooks pre-commit` etc., so the actual logic is in Python.
   - Commit: "metasphere/git_hooks: port scripts/metasphere-git-hooks"

After each module:
- Run that modules tests + full suite. Must be green.
- Commit per the plan.

After all 5:
- python -m pytest metasphere/tests/ -q 2>&1 | tail -3 — must be all passing
- git log --oneline -10 to confirm 5 new commits
- Write report at /home/openclaw/.metasphere/agents/@rewriter-small/report.md
- Send: messages send @.. !done "rewriter-small: 5 modules ported (trace/session/project/telegram-groups/git-hooks), NN tests passing, 5 commits"

CRITICAL:
- One commit per module so they can be reverted independently
- Do NOT touch scripts/
- Do NOT push
- Stdlib only (subprocess for tmux/curl/git)
- Use metasphere.io for atomic writes + locks
- Skip ports of dead bash code per PORTING dead-code list

BEGIN. Use Read, Write, Edit, Bash, Glob, Grep.
