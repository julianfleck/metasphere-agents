---
id: migration-port-openclaw-cron-jobs-and-research-agents-to-metasphere-scheduling-system-20260407
title: Migration: port openclaw cron jobs and research agents to metasphere scheduling system
priority: !high
status: pending
scope: /
created: 2026-04-07T09:41:18Z
created_by: @orchestrator
assigned_to:
started_at:
completed_at:
---

# Migration: port openclaw cron jobs and research agents to metasphere scheduling system

## Description

The original openclaw harness on spot ran a number of autonomous research
agents on a schedule (cron / systemd timers / openclaw's own scheduler).
We need to:

1. **Inventory**: enumerate every scheduled openclaw job — what runs, on
   what trigger, with what input, producing what output. Sources to check
   on spot:
   - `crontab -l` for the openclaw user
   - `systemctl --user list-timers` and any `*.timer` units in
     `~/.config/systemd/user/`
   - `~/.openclaw/openclaw.json` schedules section if any
   - `~/.openclaw/workspace/AGENTS.md` (the largest workspace file —
     17K — which documents agent procedures)
   - `~/.openclaw/skills/*` for any skill that includes scheduling
   - `~/.openclaw/state` for last-run records that hint at what was
     scheduled
2. **Pattern**: define a single canonical pattern for "scheduled research
   agent" in metasphere. Likely:
   - cron entry → `metasphere schedule add` (already exists per CLAUDE.md)
   - which spawns a child agent via `metasphere agent spawn @name --task "..."`
   - the child writes its output via `messages send @.. !done "..."`
   - results land in events log + the parent's inbox + optionally CAM
3. **Port**: rewrite each inventoried openclaw job as a metasphere
   scheduled task following the canonical pattern.
4. **Validate**: at least one ported job runs end-to-end on spot,
   produces output, and bubbles up via the messages system.

This task is part of the larger openclaw → metasphere migration bundle
(install.sh refactor + memory restore + cron port) and depends on the
findings from the openclaw research task (msg-1775537975-38355) for
canonical file/dir layout.

## Acceptance Criteria

- [ ] Inventory document committed to docs/MIGRATION-CRON.md listing
      every openclaw scheduled job with trigger, input, output.
- [ ] Canonical "scheduled research agent" pattern documented in
      docs/PATTERNS.md or templates/scheduled-agent.md.
- [ ] At least 3 openclaw jobs ported to metasphere schedule + agent
      spawn pattern, running on spot.
- [ ] One ported job validated end-to-end (cron fires → agent spawns →
      result lands in inbox/events).
- [ ] Old openclaw cron entries disabled (not deleted — kept as backup
      reference).

## Updates

- 2026-04-07T09:41:18Z [@orchestrator] Created task

## Subtasks

<!-- Add subtasks as needed -->

## Notes

