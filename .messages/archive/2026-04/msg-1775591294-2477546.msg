---
id: msg-1775591294-2477546
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T19:48:14Z
read_at:
replied_at:
completed_at:
reply_to: 
---

TASK: Port scripts/metasphere-gateway to metasphere/gateway.py + cli. EXECUTE NOW. python-rewrite branch. Commit.

Read first:
1. /home/openclaw/Code/metasphere-agents/scripts/metasphere-gateway (the source — ~870 lines, the biggest remaining bash script)
2. /home/openclaw/Code/metasphere-agents/docs/PORTING.md gateway section + invariants 7,8,15,16
3. /home/openclaw/Code/metasphere-agents/metasphere/agents.py (use session_alive, session_name_for, wake_persistent, spawn_ephemeral)
4. /home/openclaw/Code/metasphere-agents/metasphere/telegram/poller.py (use the existing poller; do NOT reimplement getUpdates)
5. /home/openclaw/Code/metasphere-agents/metasphere/telegram/inject.py
6. /home/openclaw/Code/metasphere-agents/metasphere/io.py + paths.py + events.py
7. The known gateway crash bug (the bash version is in restart-flap, see KNOWN_ISSUES.md)

Verify branch python-rewrite first.

The bash gateway has 4 jobs:
1. Maintain a persistent tmux+REPL session for @orchestrator (start/restart/health-check)
2. Poll telegram getUpdates and inject inbound messages into the orchestrator session
3. Watchdog: detect stuck-paste, auto-clear safety-hooks confirmations, force-enter on placeholder
4. Daemon mode: long-running loop tying it all together

The Python rewrite splits this cleanly:
- Session lifecycle (start/restart/health) → metasphere/gateway/session.py
- Telegram polling + injection → already exists in metasphere/telegram/poller.py + inject.py — gateway just composes them
- Watchdog (stuck-paste recovery, safety-hooks auto-approve, supervisor logging) → metasphere/gateway/watchdog.py
- Daemon entry point → metasphere/gateway/daemon.py
- Top-level public API → metasphere/gateway/__init__.py re-exports

Build /home/openclaw/Code/metasphere-agents/metasphere/gateway/ as a subpackage:

metasphere/gateway/__init__.py — re-exports start_session, restart_session, run_daemon, run_watchdog, ensure_session

metasphere/gateway/session.py:
- SESSION_NAME constant = "metasphere-orchestrator"
- start_session(paths) → bool: tmux new-session, set mouse on, history-limit 100000, env vars, exec bash respawn loop running claude --dangerously-skip-permissions
- restart_session(reason: str, paths) → None: send C-c twice + /exit + sleep, then start fresh REPL inside the same session (gateway-style "restart_claude_in_session")
- session_health(paths) → tuple[bool, int]: returns (alive, idle_seconds_since_session_activity)
- ensure_session(paths) → None: if not alive, start_session

metasphere/gateway/watchdog.py:
- check_stuck_paste(session_name, paths) → bool: capture-pane, look for "[Pasted text #N", if present and idle > 15s, force Enter. Mirror scripts/metasphere-gateway check_stuck_prompts.
- check_safety_hooks_confirmation(session_name, paths) → bool: capture-pane, look for "Do you want to proceed?" or "[plugin:safety-hooks]" or "1. Yes", auto-send "1" Enter (with 10s rate-limit via state file)
- run_watchdog(paths) → None: combines all watchdog checks, called every N seconds from the daemon

metasphere/gateway/daemon.py:
- run_daemon(paths, poll_interval=3, watchdog_interval=5) → None: while True: poll_telegram_once via metasphere.telegram.poller (forwards inbound to orchestrator session via metasphere.telegram.inject), run_watchdog, sleep poll_interval. NO restart-flap loop — the bash version exits status=1 after the watchdog runs once and that is the bug (set -e tripping on something inside the loop). Use try/except wrappers around each step so a single failure does not exit the daemon.

metasphere/cli/gateway.py:
  metasphere gateway daemon [N]    # run gateway daemon (replaces bash daemon mode)
  metasphere gateway inject "msg"  # inject directly into orchestrator session
  metasphere gateway ensure        # start session if needed
  metasphere gateway status        # check session status
  metasphere gateway restart       # restart claude inside the session

CRITICAL:
- The bash version has a daemon-flap bug (exits status 1 every ~6s in restart loop). The Python rewrite must NOT replicate this. Wrap every loop body in try/except + log + continue. Test that a single iteration failure does not exit the daemon.
- Do NOT reimplement tmux paste-submit. Shell out to scripts/metasphere-tmux-submit per invariant 15.
- Do NOT reimplement telegram getUpdates. Use metasphere.telegram.poller.poll_once.
- Use metasphere.events.log_event for supervisor.* events (matches bash schema).
- Stdlib only.

Tests at metasphere/tests/test_gateway.py:
- session_health returns (False, 0) when no session exists (mock tmux has-session)
- ensure_session calls start_session when not alive (mock subprocess)
- check_stuck_paste detects placeholder in mocked capture-pane output and triggers force Enter
- check_safety_hooks_confirmation detects the prompt patterns
- run_daemon does NOT exit on a single iteration error (mock poller.poll_once to raise; assert daemon continues)
- watchdog_interval is honored (mock time.time)

Final step:
- pytest test_gateway.py + full suite — must be all passing
- Commit on python-rewrite: "metasphere/gateway: port scripts/metasphere-gateway as a clean subpackage (session+watchdog+daemon, fixes the restart-flap bug)" with body
- Write report
- Send: messages send @.. !done "rewriter-gateway: gateway subpackage + cli + tests, NN passing, committed (fixes daemon-flap bug)"

BEGIN. Use Read, Write, Edit, Bash. Do not modify scripts/.
