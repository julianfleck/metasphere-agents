---
id: msg-1775585939-2244323
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T18:18:59Z
read_at:
replied_at:
completed_at:
reply_to: 
---

TASK: Integration test the metasphere/ Python rewrite end-to-end. EXECUTE NOW.

Read first:
1. /home/openclaw/Code/metasphere-agents/docs/PORTING.md
2. /home/openclaw/Code/metasphere-agents/metasphere/__init__.py through every file in metasphere/ (just enough to know the public surface)
3. /home/openclaw/.metasphere/config/telegram-rewrite.env — token for the parallel-track bot @metabotmetabotmetabot

Build /home/openclaw/Code/metasphere-agents/metasphere/tests/test_integration.py with cross-module tests (NOT mocked — actual file IO + actual bot API calls):

1. **End-to-end message roundtrip:** create a temp METASPHERE_DIR, spawn 2 simulated agents (just dirs), send a message from agent A to agent B via metasphere.messages.send_message, collect_inbox on B, assert the message round-trips. Verify the .msg file exists on disk with correct frontmatter (PORTING risk #3 baseline).

2. **End-to-end task lifecycle:** metasphere.tasks.create_task → start_task → update_task → complete_task. Assert active/ moves to completed/. Verify slug sanitization for a title containing slashes.

3. **End-to-end telegram smoke (LIVE bot):** call metasphere.telegram.api.send_message to the parallel-track bot (token from telegram-rewrite.env). Verify the API returns ok=true. Send a 5KB string to verify auto-chunking actually emits 2 messages with [part/total] markers. THESE TESTS HIT THE REAL BOT — mark them as `pytest.mark.live` so they can be skipped in CI but run by default here. Use a unique test marker in the message body so we can filter them out from real chat history.

4. **Concurrent message updates:** multiprocessing.Pool of 4 workers, each calling metasphere.messages.update_status on the same .msg file 25 times. Assert no torn writes, final state is one of the valid ordered outcomes, frontmatter intact.

5. **Cross-module: tasks creates a task, messages sends a !done with the task id, events.log_event records both.** Assert the events.jsonl contains one task.create event and one message.send event in order.

6. **Frontmatter compatibility:** read 3 actual existing .msg files from /home/openclaw/Code/metasphere-agents/.messages/inbox/ (the bash-emitted ones). Verify metasphere.messages.read_message parses them without error and round-trips them via write_message.

For each test that crosses to the bot: print the chat_id and the message_id of what was sent so we have a paper trail.

Final step:
- Run: python -m pytest metasphere/tests/test_integration.py -v -s 2>&1 | tail -40
- Write report to /home/openclaw/.metasphere/agents/@tester-integration/report.md with: total tests, pass/fail count, any compatibility breaks vs existing bash files, the bot message_ids that were sent
- Send: messages send @.. !done "tester-integration: N integration tests, M passing, see report.md"

BEGIN. You may write new test files but do NOT modify any existing metasphere/ source files. Use Read, Write, Bash. The bot WILL receive real test messages — that is intentional.
