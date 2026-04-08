---
id: msg-1775589303-2365548
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T19:15:03Z
read_at:
replied_at:
completed_at:
reply_to: 
---

TASK: Port scripts/metasphere-schedule to metasphere/schedule.py + cli. EXECUTE NOW. On python-rewrite branch. Commit your work.

Read first:
1. /home/openclaw/Code/metasphere-agents/scripts/metasphere-schedule (~500 lines bash, the cron-style dispatcher)
2. /home/openclaw/Code/metasphere-agents/docs/PORTING.md (schedule section + risk #2 eval-on-disk + risk #3 file races + the recent subshell-pipe disaster)
3. /home/openclaw/Code/metasphere-agents/metasphere/io.py (read_json, write_json, file_lock — use these)
4. /home/openclaw/Code/metasphere-agents/metasphere/paths.py (paths.schedule_jobs)
5. /home/openclaw/Code/metasphere-agents/metasphere/events.py (log_event)

Verify branch: `git branch --show-current` → must say python-rewrite. Abort if not.

Build /home/openclaw/Code/metasphere-agents/metasphere/schedule.py with:
- Job dataclass: id, source, source_id, agent_id, name, enabled, kind, cron_expr, tz, payload_kind, payload_message, model, session_target, wake_mode, imported_at, last_fired_at, next_run, command, full_command. Mirror the on-disk schema EXACTLY (verify against /home/openclaw/.metasphere/schedule/jobs.json).
- load_jobs(paths) → list[Job]: read jobs.json under file_lock
- save_jobs(jobs, paths): atomic write_json under file_lock with shrink-detection guard (refuse to write 0 jobs if input had >0)
- cron_should_fire(expr, tz, last_fired_at, now) → bool: use croniter (which IS installed system-wide). 180s window. prev_epoch > last_fired_at.
- run_due_jobs(paths) → list[FireResult]: iterate jobs, check should_fire, dispatch, update last_fired_at
- resolve_target_agent(job) → str: same role-prefix mapping as the bash patch (research-monitor:* → @research-*, polymarket:* → @polymarket, spot:autonomous-exploration → @explorer, rage-changelog → @rage-changelog, "Morning briefing" → @briefing)
- dispatch_to_agent(target_agent, payload) → bool: if target has MISSION.md → call metasphere-wake (subprocess to scripts/metasphere-wake), else fall back to messages.send_message with !task
- list_jobs(paths) → list[Job]: for the CLI list verb

Build /home/openclaw/Code/metasphere-agents/metasphere/cli/schedule.py mirroring:
  metasphere-schedule              # default = list
  metasphere-schedule list
  metasphere-schedule run           # one tick
  metasphere-schedule daemon [N]    # loop forever, default 60s
  metasphere-schedule add ...       # if bash supports it, mirror
  metasphere-schedule enable <id>
  metasphere-schedule disable <id>

Critical:
- NEVER eval. The bash version has eval on disk-stored input — that is the security finding from PORTING risk #2. Python uses subprocess.run with explicit argv only.
- File locking on every read-modify-write of jobs.json
- Atomic write via metasphere.io.write_json which already does flock + tmp+rename
- Shrink-detection guard: if input N>0 and output 0, raise instead of writing
- Log fire events via metasphere.events.log_event with type "schedule.cron_fire"

Tests at /home/openclaw/Code/metasphere-agents/metasphere/tests/test_schedule.py (use tmp_paths fixture):
- load + save roundtrip preserves all fields
- shrink-detection guard refuses zero-write when input had jobs
- cron_should_fire returns True for an expression due in the last 180s with last_fired_at=0
- cron_should_fire returns False when last_fired_at >= prev_epoch
- resolve_target_agent maps research-monitor:brand-mentions → @research-brand-mentions
- run_due_jobs updates last_fired_at on fired jobs
- dispatch_to_agent prefers metasphere-wake when MISSION.md exists (mock subprocess.run, assert called with the right argv)

Final step:
- Run `python -m pytest metasphere/tests/test_schedule.py -v 2>&1 | tail -20` — must be all passing
- Run `python -m pytest metasphere/tests/ -q 2>&1 | tail -3` — total suite must still be green
- Commit on python-rewrite: `git add metasphere/schedule.py metasphere/cli/schedule.py metasphere/tests/test_schedule.py && git commit -m "metasphere: port scripts/metasphere-schedule (eval removed, file locks, shrink-detection)"` with a body listing the bash bugs killed
- Write report to /home/openclaw/.metasphere/agents/@rewriter-schedule/report.md
- Send: messages send @.. !done "rewriter-schedule: schedule.py + cli + tests, NN passing, committed"

BEGIN. Use Read, Write, Edit, Bash. Do not modify scripts/.
