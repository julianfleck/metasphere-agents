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

## Inventory (from spot, 2026-04-07)

`~/.openclaw/cron/jobs.json` defines 50+ jobs, ~17 enabled. Backup copies
at `jobs.json.bak`, `.bak2`, `.bak3` show schema is stable. Each job has:
`id`, `agentId`, `name`, `enabled`, `schedule.{kind,expr,tz}`,
`sessionTarget`, `wakeMode`, `payload.{kind,message,model}`, and a
`state` block tracking `nextRunAtMs`, `lastRunAtMs`, `lastStatus`,
`consecutiveErrors`, etc.

Currently enabled jobs (all under agentId "main", schedules in Europe/Berlin
unless noted):
- `Morning briefing`               `0 10 * * 1-5`  weekdays 10am — multi-section briefing (research synthesis, calendar, Linear, gmail, newsletters)
- `spot:autonomous-exploration`    `0 10,12,14,16,18,20,22,0 * * *`  every 2 hours
- `research-monitor:brand-mentions`        `0 5 * * *`     daily 5am
- `research-monitor:memory-architectures`  `0 5 * * 5`     Fri 5am
- `research-monitor:retrieval-architectures` `5 5 * * 5`   Fri 5:05am
- `research-monitor:agentic-reasoning`     `10 5 * * 5`    Fri 5:10am
- `research-monitor:evaluation-governance` `15 5 * * 5`    Fri 5:15am
- `research-monitor:divergence-engines`    `0 5 * * 1`     Mon 5am
- `research-monitor:ephemeral-interfaces`  `5 5 * * 1`     Mon 5:05am
- `research-monitor:residency-programs`    `20 5 * * *`    daily 5:20am
- `research-monitor:job-opportunities`     `30 5 * * *`    daily 5:30am
- `research-monitor:accelerator-programs`  `40 5 * * *`    daily 5:40am
- `rage-changelog-update`          `30 9 * * 1-5`   weekdays 9:30am
- `polymarket:trading-run`         `17 */2 * * *` (America/Vancouver)
- `polymarket:quick-scan`          `*/30 * * * *` (America/Vancouver)
- `polymarket:daily-summary`       `0 9,21 * * *`   9am + 9pm

Configured agent identities under `~/.openclaw/agents/`:
- `main` — primary, has `profile/` and `sessions/` subdirs
- `night`
- `research-gather`
- `research-synthesize`
- `coding-integration`
- `coding-simple`

Many test/dev jobs are disabled (rage-dev-cycle-{1..5}, dreamer tests,
subagent checks, etc.) — port only enabled ones.

## Schema migration notes

openclaw cron payload format → metasphere equivalent:
- `agentId` → which child agent to spawn (`metasphere agent spawn @<name>`)
- `schedule.expr` + `tz` → `metasphere schedule add 'cmd' --cron '<expr>' --tz '<tz>'`
- `payload.message` → the prompt the spawned agent receives
- `payload.model` → optional model override
- `sessionTarget: isolated` → child agent in fresh sandbox
- `wakeMode: next-heartbeat` → fire-on-trigger vs wait-for-event semantics

State tracking on the metasphere side: `lastRunAtMs`, `lastStatus`,
`consecutiveErrors` should map to event-log entries (`metasphere events log
schedule.<jobId>.run ok|error ...`) plus a state file under
`~/.metasphere/state/schedule/<jobId>.json`.

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

