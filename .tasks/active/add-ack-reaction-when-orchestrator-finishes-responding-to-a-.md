---
id: add-ack-reaction-when-orchestrator-finishes-responding-to-a-
title: Add 👍 ack reaction when @orchestrator finishes responding to a user message
priority: !normal
status: pending
scope: /.
project: default
created: 2026-04-08T07:41:19Z
created_by: @orchestrator
assigned_to: 
started_at: 
updated_at: 2026-04-08T13:44:10Z
completed_at: 
last_pinged_at: 2026-04-08T12:02:15Z
ping_count: 2
---
# Add 👍 ack reaction when @orchestrator finishes responding to a user message

## Description

Currently the bot sets a 👀 reaction on incoming user messages (read receipt) but does NOT signal when the orchestrator has finished responding. Add a 👍 reaction on the user's original message after the orchestrator's reply has been delivered, so the user knows their request was acted on (not just received). Implementation: in metasphere/gateway/daemon.py _poll_once, after dispatching a non-slash message to the orchestrator and the posthook has forwarded a reply, call set_message_reaction with 👍 on the original message_id. Edge case: orchestrator may produce multiple responses across heartbeat ticks; only react after the FIRST substantive response, not on every silent tick.

## Updates

- 2026-04-08T07:41:19Z Created task

## Spec

Currently the gateway daemon adds an 👀 reaction to an incoming user message immediately on receipt (commit-pending, fix for the python-cutover regression). Julian asked for a follow-up: when @orchestrator's response actually lands on Telegram, replace the eye with a 👍 (or any "produced an answer" emoji) so he can see at a glance that a message has been (a) read and (b) acknowledged-with-a-real-reply, vs just sitting in the queue.

Implementation sketch:

1. Thread `message_id` from the incoming Telegram update through `submit_to_tmux` and into the agent's per-turn context (probably via `~/.metasphere/state/last_user_msg.json` or by extending the context-injection hook). Currently only the text is propagated.
2. In `metasphere/cli/posthook.py` (the Stop hook that forwards `last_text` to Telegram), look up the message_id of the most-recent user message that triggered this turn. After successfully sending the response, call `set_message_reaction(chat_id, message_id, "👍")` (which replaces the existing 👀 — Telegram's setMessageReaction *replaces* per chat-msg).
3. Edge cases to handle:
   - Heartbeat-triggered turns: there is no user message to react to; skip.
   - Turns triggered by `agent.wake` from a child !done: react to the child's status not the user. Probably skip the user-side reaction here too.
   - Multi-message responses (long replies that get chunked): only reaction-update on the *last* message of the chain.
   - Rate limit: if the user sends 3 messages in a burst and one Stop hook fires, only the last gets the 👍. Acceptable.
4. Test: extend test_telegram_inject (or add test_posthook_reaction.py) to assert that after a fake user message → fake Stop hook, set_message_reaction is called with 👍 and the right msg id.

Out of scope for this task: more granular reaction states (🤔 thinking, ⚠️ error, 🛑 blocked). Those can be a follow-up if the 👀→👍 pattern proves useful.

Reference: regression-fix commit (eye reaction in daemon._poll_once) introduces the 👀 part. This task closes the loop.