---
id: make-agent-tree-actually-look-like-a-tree-20260406
title: Make agent tree actually look like a tree
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
last_pinged_at: 2026-04-08T12:02:19Z
ping_count: 2
---
# Make agent tree actually look like a tree

## Description

Currently 'metasphere agents tree' produces a flat list: each agent printed as a card with status/scope/parent/task. The output is called 'tree' but has no tree structure. Real fix: render agents as an actual ASCII tree, indented by parent → child relationship, using the parent field that's already in each agent's identity. Top-level: agents with no parent (e.g. @orchestrator). Children indented under their parent, recursively. Cosmetic but useful for understanding the spawn hierarchy at a glance. !low priority — visual polish, not blocking.

## Acceptance Criteria

- [ ] Criteria 1
- [ ] Criteria 2

## Updates

- 2026-04-07T03:51:00Z [@orchestrator] Created task

## Subtasks

<!-- Add subtasks as needed -->

## Notes