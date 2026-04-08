---
id: metasphere-chat-attach-tmux-to-orchestrator-session-20260406
title: "metasphere chat: attach tmux to orchestrator session"
priority: !normal
status: pending
scope: /
project: default
created: 2026-04-07T03:51:00Z
created_by: @orchestrator
assigned_to: 
started_at: 
updated_at: 2026-04-08T13:44:11Z
completed_at: 
last_pinged_at: 2026-04-08T12:02:21Z
ping_count: 2
---
# metasphere chat: attach tmux to orchestrator session

## Description

'metasphere chat' currently returns 'Unknown command'. Goal: a CLI subcommand that attaches the user's terminal to the live tmux session running the orchestrator REPL. So instead of going through Telegram, the operator can sit at the terminal and watch the orchestrator's claude REPL in real-time, type directly into it, see streaming output. Useful for debugging, observing reasoning, intervening directly. Implementation: 'metasphere chat' execs 'tmux attach -t metasphere-orchestrator' (or whatever the session name is). Detach with the standard tmux prefix-d. Should also handle the case where the session doesn't exist (start it via ensure_session).

## Acceptance Criteria

- [ ] Criteria 1
- [ ] Criteria 2

## Updates

- 2026-04-07T03:51:00Z [@orchestrator] Created task

## Subtasks

<!-- Add subtasks as needed -->

## Notes