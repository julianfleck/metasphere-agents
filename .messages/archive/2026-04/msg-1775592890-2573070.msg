---
id: msg-1775592890-2573070
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T20:14:50Z
read_at:
replied_at:
completed_at:
reply_to: 
---

TASK: Full integration test pass against the entire metasphere/ python rewrite. EXECUTE NOW. python-rewrite branch.

The package is now feature-complete (foundation + 8 core modules + 3 subpackages + 5 utility modules + CLI shims, 158 unit/integration tests passing). Run a comprehensive end-to-end pass that simulates the full bash↔python interop coexistence.

Read first:
1. /home/openclaw/Code/metasphere-agents/metasphere/ (skim the public surface — 25 modules)
2. /home/openclaw/Code/metasphere-agents/docs/PORTING.md
3. /home/openclaw/.metasphere/agents/@tester-integration/report.md (the wave-1 integration test report — match its rigor)

Build /home/openclaw/Code/metasphere-agents/metasphere/tests/test_integration_2.py with these tests:

1. **Cross-module: spawn → message → reply chain**
   - Use metasphere.agents.spawn_ephemeral with METASPHERE_SPAWN_NO_EXEC=1 (no real claude)
   - Send a message via metasphere.messages.send_message FROM the spawned agent TO the orchestrator
   - Collect orchestrator inbox via metasphere.messages.collect_inbox
   - Reply via metasphere.messages.reply_to_message
   - Assert original is marked replied AND reply has reply_to set

2. **Backward compat: bash-emitted files round-trip through Python**
   - Read existing real .msg files from /home/openclaw/Code/metasphere-agents/.messages/inbox/ via metasphere.messages.read_message
   - Read at least 5 different files
   - Round-trip each through write_message and assert byte-equivalent frontmatter (modulo whitespace normalization)
   - Read existing real .tasks/active/*.md and round-trip

3. **Live telegram smoke (use the parallel-track bot from telegram-rewrite.env)**
   - metasphere.telegram.api.send_message with a unique nonce in the body
   - Verify ok=true
   - Send 6KB message → assert exactly 2 chunks with [1/2] [2/2] markers
   - Tag all test messages with INTEGRATION-TEST-2 prefix so they can be filtered

4. **Schedule daemon dry-run**
   - Load /home/openclaw/.metasphere/schedule/jobs.json via metasphere.schedule.load_jobs
   - Assert all 16 jobs load with non-null cron_expr
   - Call metasphere.schedule.cron_should_fire for each with a synthetic now-clock; assert no exceptions
   - Call metasphere.schedule.resolve_target_agent on each job name; assert it maps to a valid agent name
   - Do NOT actually fire any job

5. **Memory recall over real corpus**
   - metasphere.memory.recall("polymarket trading") with default strategies (TokenOverlap + Cam)
   - Assert at least 1 hit
   - metasphere.memory.context_for("rewrite cleanup", budget_chars=1024)
   - Assert returned text is non-empty and <= 1024 chars

6. **Heartbeat one-shot dry-run**
   - metasphere.heartbeat.heartbeat_once(invoke_agent=False) — DO NOT invoke a real agent
   - Assert no exception, state file written

7. **Posthook simulation**
   - Construct a fake Stop hook payload with a known transcript_path containing a fake JSONL
   - Call metasphere.posthook.run_posthook with that payload
   - Mock metasphere.telegram.api.send_message; assert called once with the expected text
   - Re-run with same text; assert dedupe (mock called only once total)

8. **Gateway daemon does not flap**
   - metasphere.gateway.run_daemon with poll_interval=1 and max_iterations=3 (add this flag to gateway.daemon.run_daemon if not present, or run in a thread+kill)
   - Mock telegram.poller.poll_once to raise on iteration 2
   - Assert daemon completes 3 iterations without exiting (wraps the error and continues)

9. **Concurrency: messages multiproc updates**
   - 4 processes x 25 iters x update_status on the same .msg
   - Final state coherent, frontmatter intact

10. **Trace capture roundtrip**
    - metasphere.trace.capture_trace(["echo", "hello"]) → Trace with exit_code=0, stdout file containing "hello"
    - metasphere.trace.search_traces("hello") → finds it

For each live telegram test, print the message_id so we have a paper trail.

Final step:
- Run python -m pytest metasphere/tests/test_integration_2.py -v -s 2>&1 | tail -50
- Run the FULL suite: python -m pytest metasphere/tests/ -q 2>&1 | tail -3 — must be all green
- Commit on python-rewrite: "metasphere/tests: integration_2 — full cross-module end-to-end pass" with body listing the 10 tests
- Write report at /home/openclaw/.metasphere/agents/@tester-integration-2/report.md including the bot message_ids
- Send: messages send @.. !done "tester-integration-2: 10 e2e tests, X passing, see report.md"

CRITICAL: do not modify metasphere/ source files. Only write the new test file. Use Read, Write, Bash. Do not push.

BEGIN.
