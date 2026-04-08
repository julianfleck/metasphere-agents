---
id: memory-consolidation-v3-scan-learningsmd-daily-logs-for-stal
title: "Memory consolidation v3: scan LEARNINGS.md + daily logs for staleness, nudge agents to update on responsible turns, prune ancient entries with summary rollup"
priority: !normal
status: pending
scope: /.
project: default
created: 2026-04-08T11:34:13Z
created_by: @orchestrator
assigned_to: 
started_at: 
updated_at: 2026-04-08T13:44:10Z
completed_at: 
last_pinged_at: 2026-04-08T12:02:20Z
ping_count: 1
---
# Memory consolidation v3: scan LEARNINGS.md + daily logs for staleness, nudge agents to update on responsible turns, prune ancient entries with summary rollup

## Description

v1: posthook auto-close on agent clean exit (shipped). v2: consolidation cycle for tasks (shipped, every 5min). v3: extend the consolidation cycle to ALSO scan agent memory files — LEARNINGS.md and ~/.metasphere/agents/$id/daily/YYYY-MM-DD.md. Detect: agents with empty/missing daily logs despite having had activity, LEARNINGS.md entries that haven't been touched in N days, daily logs from past dates that should be summarized + rolled into LEARNINGS. Action: nudge the responsible agent on its next turn ('you didn't update LEARNINGS for X today, want to add an entry?'), or auto-summarize old entries with an LLM pass. Depends on a clear model of 'what counts as a notable turn that should land in memory'.

## Updates

- 2026-04-08T11:34:13Z Created task