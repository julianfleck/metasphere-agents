---
id: msg-1775590622-2439836
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T19:37:02Z
read_at:
replied_at:
completed_at:
reply_to: 
---

TASK: Port scripts/metasphere-heartbeat to metasphere/heartbeat.py + cli. EXECUTE NOW. python-rewrite branch. Commit.

Read first:
1. /home/openclaw/Code/metasphere-agents/scripts/metasphere-heartbeat (the source — proactive monitoring daemon, ~270 lines)
2. /home/openclaw/Code/metasphere-agents/docs/PORTING.md heartbeat section
3. /home/openclaw/Code/metasphere-agents/metasphere/messages.py (collect_inbox)
4. /home/openclaw/Code/metasphere-agents/metasphere/tasks.py (list_tasks)
5. /home/openclaw/Code/metasphere-agents/metasphere/agents.py (list_agents, session_alive, session_name_for)
6. /home/openclaw/Code/metasphere-agents/metasphere/io.py + paths.py + events.py
7. /home/openclaw/.config/systemd/user/metasphere-heartbeat.service (the systemd unit shape we need to keep compatible)

Verify branch python-rewrite first. Abort if not.

Build /home/openclaw/Code/metasphere-agents/metasphere/heartbeat.py with:
- check_urgent_messages(paths) → list[Message]: scan all .messages/inbox/*.msg for !urgent + status=unread; return matches
- check_blocked_agents(paths) → list[AgentRecord]: scan ~/.metasphere/agents/@*/status for status starting with "waiting:" or "blocked:"
- check_urgent_tasks(paths) → tuple[int, int]: returns (urgent_count, total_count)
- build_agent_context(agent="@orchestrator", paths) → str: assembles the heartbeat context block with last telegram, active tasks, agent statuses, pending messages, decision footer. Calls metasphere.context.build_context for the bulk and prepends "# HEARTBEAT" header.
- invoke_agent_heartbeat(agent="@orchestrator", paths) → bool: if metasphere.agents.session_alive(session_name_for(agent)) → submit_to_tmux via subprocess to scripts/metasphere-tmux-submit with the built context. Else fall back to claude -p one-shot via subprocess (with --allowedTools sandbox).
- log_status_to_disk(paths): record "alive at <iso ts>" to ~/.metasphere/state/heartbeat_last_run
- heartbeat_once(paths, invoke_agent: bool = False) → None: run urgent + blocked + tasks checks, optionally invoke agent. State file dedupe so we don\u2019t re-notify the same urgent message twice (mirror the bash $METASPHERE_DIR/heartbeat_state behavior).
- heartbeat_daemon(paths, interval_seconds: int = 30, invoke_agent: bool = False) → None: while True: heartbeat_once; sleep interval

Build /home/openclaw/Code/metasphere-agents/metasphere/cli/heartbeat.py with:
  metasphere heartbeat            # one-shot
  metasphere heartbeat daemon [N] # default 30s
  --invoke-agent                  # set HEARTBEAT_INVOKE_AGENT=true equivalent

Tests at /home/openclaw/Code/metasphere-agents/metasphere/tests/test_heartbeat.py (use tmp_paths):
- check_urgent_messages finds unread !urgent messages
- check_blocked_agents finds agents with status "waiting:" or "blocked:"
- check_urgent_tasks counts correctly
- build_agent_context prepends "# HEARTBEAT" header and includes the standard sections
- heartbeat_once dedupes via the state file (call twice on the same urgent message; only first call should notify)
- invoke_agent_heartbeat with no live tmux session falls back to one-shot path (mock subprocess)

Critical:
- The systemd unit file ~/.config/systemd/user/metasphere-heartbeat.service currently calls scripts/metasphere-heartbeat daemon 300 with HEARTBEAT_INVOKE_AGENT=true. The Python rewrite must support being called via `python -m metasphere.cli.heartbeat daemon 300` AND via the bash script (since cutover is later). So the new module must be feature-equivalent to the bash version.
- File locking on the dedupe state file via metasphere.io
- Use metasphere.events.log_event for any internal events
- Stdlib only

Final step:
- pytest test_heartbeat.py + full suite
- Commit on python-rewrite: "metasphere/heartbeat: port scripts/metasphere-heartbeat (proactive monitoring daemon)" with body
- Write report
- Send: messages send @.. !done "rewriter-heartbeat: heartbeat.py + cli + tests, NN passing, committed"

BEGIN.
