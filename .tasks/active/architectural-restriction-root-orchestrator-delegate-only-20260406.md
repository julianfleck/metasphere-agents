---
id: architectural-restriction-root-orchestrator-delegate-only-20260406
title: "Architectural restriction: root orchestrator delegate-only"
priority: !low
status: pending
scope: /
project: default
created: 2026-04-07T03:51:00Z
created_by: @orchestrator
assigned_to: 
started_at: 
updated_at: 2026-04-08T13:44:10Z
completed_at: 
last_pinged_at: 2026-04-08T12:02:16Z
ping_count: 2
---
# Architectural restriction: root orchestrator delegate-only

## Description

Architectural rule: the root @orchestrator REPL should DELEGATE all substantive work to spawned ephemeral agents, not do hands-on coding/editing itself. The orchestrator becomes a planner/coordinator/reviewer; ephemerals do the implementation. Demoted to !low because today's high-iteration UX work made direct edits more efficient than spawn-per-change. Revisit when the team scales beyond one operator and the orchestrator needs to remain a stable coordination point rather than a busy implementer.

## Acceptance Criteria

- [ ] Criteria 1
- [ ] Criteria 2

## Updates

- 2026-04-07T03:51:00Z [@orchestrator] Created task

## Subtasks

<!-- Add subtasks as needed -->

## Notes