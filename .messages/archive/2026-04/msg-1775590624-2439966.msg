---
id: msg-1775590624-2439966
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T19:37:04Z
read_at:
replied_at:
completed_at:
reply_to: 
---

TASK: Port scripts/metasphere-posthook to metasphere/posthook.py + cli. EXECUTE NOW. python-rewrite branch. Commit.

Read first:
1. /home/openclaw/Code/metasphere-agents/scripts/metasphere-posthook (~110 lines, the Stop hook)
2. /home/openclaw/Code/metasphere-agents/docs/PORTING.md posthook section
3. /home/openclaw/Code/metasphere-agents/metasphere/telegram/api.py (use send_message for outbound)
4. /home/openclaw/Code/metasphere-agents/metasphere/io.py + paths.py + events.py
5. /home/openclaw/Code/metasphere-agents/.claude/settings.local.json (so you understand how Stop hooks are wired into claude-code)

Verify branch python-rewrite first.

Build /home/openclaw/Code/metasphere-agents/metasphere/posthook.py with:
- read_stop_hook_payload(stdin_bytes: bytes) → dict: parse the JSON Stop hook payload from claude-code (fields: session_id, transcript_path, hook_event_name, stop_hook_active)
- extract_last_assistant_text(transcript_path: Path) → str | None: read the JSONL transcript, walk backwards, find the most recent {"type":"assistant", ...} message, concatenate text content blocks. Mirror the bash awk+jq pipeline.
- should_skip_silent_tick(text: str) → bool: per the CLAUDE.md "Heartbeat Turn Etiquette", silent ticks should produce no text. The empty short-circuit handles that — but the function is here as an explicit hook for any future filter logic. Default: skip if empty after strip.
- route_to_telegram(text: str, paths: Paths) → None: dedupe via state hash (~/.metasphere/state/posthook_last_sent), write last_hash atomically, call metasphere.telegram.api.send_message via the parallel-track bot. Log failures to ~/.metasphere/state/posthook_telegram_errors.log instead of swallowing.
- track_turn_completion(agent: str, paths: Paths) → None: bump the per-agent activity counter at ~/.metasphere/agents/<agent>/activity.json (under flock), update updated_at, log heartbeat event every 10 turns
- run_posthook(stdin_bytes: bytes, paths: Paths) → int: top-level entry — read payload, extract text, route, track. Returns exit code (always 0 — the Stop hook must never break the host).

Build /home/openclaw/Code/metasphere-agents/metasphere/cli/posthook.py:
  python -m metasphere.cli.posthook   # reads stdin Stop-hook payload, runs posthook, exits 0
  This is the entry point that .claude/settings.local.json calls.

Tests at /home/openclaw/Code/metasphere-agents/metasphere/tests/test_posthook.py:
- read_stop_hook_payload parses a sample JSON payload
- extract_last_assistant_text walks a fake JSONL transcript and returns the right text (multi-block content)
- extract_last_assistant_text returns None on empty transcript
- should_skip_silent_tick(empty) is True, ("real reply text") is False
- route_to_telegram dedupes via state file (mock metasphere.telegram.api.send_message; assert called once for unique text, zero times for duplicate)
- route_to_telegram writes errors to log on API failure (mock api.send_message to raise; assert log line written)
- track_turn_completion increments activity.json under lock (multiprocess concurrency test optional)

Critical:
- DO NOT actually send to Telegram in tests (mock metasphere.telegram.api.send_message)
- The hook must never raise — wrap everything in try/except and return 0
- Use metasphere.io for atomic state file writes
- Use the parallel-track bot via metasphere.telegram.api.send_message (which already reads telegram-rewrite.env)

Final step:
- pytest test_posthook.py + full suite
- Commit on python-rewrite: "metasphere/posthook: port Stop hook (assistant->telegram routing + activity tracking)" with body
- Write report
- Send: messages send @.. !done "rewriter-posthook: posthook.py + cli + tests, NN passing, committed"

BEGIN.
