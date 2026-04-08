---
id: msg-1775589309-2365772
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T19:15:09Z
read_at:
replied_at:
completed_at:
reply_to: 
---

TASK: Port scripts/metasphere-spawn AND scripts/metasphere-wake into metasphere/agents.py (single module owning the spawn/wake lifecycle). EXECUTE NOW. On python-rewrite branch. Commit.

Read first:
1. /home/openclaw/Code/metasphere-agents/scripts/metasphere-spawn (~190 lines)
2. /home/openclaw/Code/metasphere-agents/scripts/metasphere-wake (~195 lines, the persistent-agent tmux+REPL launcher)
3. /home/openclaw/Code/metasphere-agents/docs/PORTING.md sections on spawn and wake, plus invariants 7,8,15,16 (tmux session naming + harness hash + paste-submit + wake protocol)
4. /home/openclaw/Code/metasphere-agents/metasphere/io.py
5. /home/openclaw/Code/metasphere-agents/metasphere/paths.py
6. /home/openclaw/Code/metasphere-agents/metasphere/identity.py
7. /home/openclaw/Code/metasphere-agents/metasphere/events.py

Verify branch python-rewrite first. Abort if not.

Build /home/openclaw/Code/metasphere-agents/metasphere/agents.py with:
- AgentRecord dataclass: name (with @ prefix), scope, parent, status, spawned_at, mission_path (Optional[Path]), session_name (computed), pid_file (Optional[Path])
- list_agents(paths) → list[AgentRecord]: enumerate ~/.metasphere/agents/@*/
- is_persistent(agent) → bool: True if MISSION.md exists
- session_name_for(agent_name) → str: "metasphere-<name>" (without @)
- session_alive(name) → bool: tmux has-session check
- spawn_ephemeral(agent_name, scope, task, parent, paths) → AgentRecord: headless one-shot. Creates dir, writes harness.md, status, scope, parent, spawned_at. Sends initial !task message via metasphere.messages.send_message. nohup-launches `claude -p "$(cat harness)" --dangerously-skip-permissions` detached, writes pid_file + output_log. Logs agent.spawn event. Mirrors scripts/metasphere-spawn behavior.
- wake_persistent(agent_name, first_task=None, paths) → AgentRecord: tmux+REPL launcher. If session already alive, optionally inject task and return. Else: tmux new-session, set env vars, exec bash respawn loop running claude --dangerously-skip-permissions. Poll tmux capture-pane for "bypass permissions" indicator (ready signal) up to 15s. Then send Ctrl-U (clear stray buffer) and submit_to_tmux the first task if provided. Logs agent.session event. Mirrors scripts/metasphere-wake behavior including the cold-start ready-poll and Ctrl-U cleanup.
- gc_dormant(paths, max_idle_seconds=86400) → list[str]: identify persistent agents whose tmux session idle time exceeds threshold. Returns names; does NOT kill (caller decides).

Build /home/openclaw/Code/metasphere-agents/metasphere/cli/agents.py with:
  metasphere-spawn @name /scope/ "task" [@parent]
  metasphere-wake @name ["first task"]
  metasphere-wake --list
  metasphere-wake --status
  agents list (alias)
  agents status

Critical:
- The bash scripts use `submit_to_tmux` from scripts/metasphere-tmux-submit. Python does NOT reimplement this — it shells out to scripts/metasphere-tmux-submit (the tmux paste-race contract is invariant 15 and stays in bash). Use subprocess.run with shell=False.
- The METASPHERE_SPAWN_NO_EXEC=1 env var opt-out from spawn must be honored.
- Invariant 7: session naming "metasphere-<name>" exact match.
- Invariant 16: when wake_persistent is called and session is already alive, just inject the task — do not start a new session.
- File locking on agent metadata writes (status file, spawned_at).

Tests at /home/openclaw/Code/metasphere-agents/metasphere/tests/test_agents.py:
- session_name_for(@briefing) == "metasphere-briefing"
- list_agents finds agents created in tmp_paths
- is_persistent True only when MISSION.md exists
- spawn_ephemeral writes the right files and respects METASPHERE_SPAWN_NO_EXEC=1 (mock subprocess to assert NOT called)
- wake_persistent without an alive session calls subprocess.run on tmux new-session (mock subprocess; assert command shape)
- wake_persistent with first_task injects via submit_to_tmux subprocess (mock subprocess)
- gc_dormant returns names whose idle time exceeds the threshold (mock tmux display-message)

Final step:
- pytest test_agents.py + full suite
- Commit: `git add metasphere/agents.py metasphere/cli/agents.py metasphere/tests/test_agents.py && git commit -m "metasphere: port spawn + wake into metasphere.agents (lifecycle module)"` with body listing what owns what
- Write report
- Send: messages send @.. !done "rewriter-spawn: agents.py + cli + tests, NN passing, committed"

BEGIN.
