---
id: tune-metasphere-context-truncation-budgets-20260406
title: Tune metasphere-context truncation budgets
priority: !normal
status: pending
scope: /
project: default
created: 2026-04-07T03:55:32Z
created_by: @orchestrator
assigned_to: 
started_at: 
updated_at: 2026-04-08T13:44:11Z
completed_at: 
last_pinged_at: 2026-04-08T12:02:24Z
ping_count: 2
---
# Tune metasphere-context truncation budgets

## Description

metasphere/context.py uses DEFAULT_SECTION_BUDGET=2048 bytes for ALL 8 sections injected per turn (status, drift, project, telegram, messages, tasks, events, memory). One uniform number for sections with very different natural sizes is wasteful: events compresses to ~600 bytes, messages frequently runs to 13k+ before truncation. Tune: measure actual section sizes from real heartbeats over a few days, set per-section budgets that capture the high-signal parts and drop the rest. Optionally make budgets adaptive (more for messages when there are many unread, less when empty). Real win for context-window efficiency. Effort: medium. Probably needs a small instrumentation pass first to gather size distributions.

## Acceptance Criteria

- [ ] Criteria 1
- [ ] Criteria 2

## Updates

- 2026-04-07T03:55:32Z [@orchestrator] Created task

## Subtasks

<!-- Add subtasks as needed -->

## Notes