# Task Backlog Audit — 2026-04-08

@task-hygiene-fix · spawned by @orchestrator

## Summary

23 task files in `.tasks/active/` + 5 orphaned files trapped inside
slugify-bug subdirectories. Cross-referenced every task title against
`git log --all --oneline` on this repo.

| verdict | count |
|---|---|
| completed (clear git evidence — work landed, never archived) | 10 |
| stale (no commit evidence, leave pending) | 13 |
| orphaned (malformed slug, pre-fix slugify bug) | 5 |
| **total** | **28** |

**This is a systemic failure.** 10/23 active tasks already shipped but
were never closed by the agent that did the work. Root cause and fix
in T3 below.

## Audit table

| task_id | created | status | git evidence | verdict |
|---|---|---|---|---|
| add-ack-reaction-when-orchestrator-finishes-responding-to-a- | 2026-04-08 | pending | only `👀` read-reaction commits (8ba2ef7, dfc71ad); no `👍` ack-on-finish | stale |
| architectural-restriction-root-orchestrator-delegate-only-20260406 | 2026-04-07 | pending | none | stale |
| audit-agent-ephemerality--cleanup-20260406 | 2026-04-07 | pending | none | stale |
| auto-update-mechanism-for-distributed-agents-20260406 | 2026-04-07 | pending | 7fd0d1e + 75742d7 + 5874804 + d4f5601 (auto-update 1..4/4) | **completed** |
| configurable-auto-updates-scheduled-metasphere-update-job-fo | 2026-04-07 | pending | same auto-update 1..4/4 commits | **completed** |
| cut-systemd-metasphere-gateway-over-from-bash-to-python-daemon-20260408 | 2026-04-08 | pending | 0121b6d (port gateway), 8d06872 (flip systemd units), 9c30f9e (harness_hash baseline) | **completed** |
| fix-metasphere-daemon-status-accuracy-20260406 | 2026-04-07 | pending | none specific | stale |
| fix-tasks-slug-sanitization-for-slash-chars-20260406 | 2026-04-07 | pending | a71dd43 (document) + b6abe4c (`slugify` in metasphere/tasks.py replaces `/` with `-`) | **completed** |
| fractal-spawning-any-agent-can-spawn-sub-agents-20260406 | 2026-04-07 | pending | e74c540 (port spawn+wake into metasphere.agents lifecycle module) | **completed** |
| install-walk-operator-through-one-time-forum-supergroup-crea | 2026-04-08 | pending | a94cac8 (telegram-groups: non-interactive setup + verify subcommands) | **completed** |
| installsh-stopdisablemask-metasphere-telegramservice-when-ga | 2026-04-08 | pending | c9417a2 partial (disable legacy units) but task is fresh today | stale |
| make-agent-tree-actually-look-like-a-tree-20260406 | 2026-04-07 | pending | none | stale |
| metasphere-chat-attach-tmux-to-orchestrator-session-20260406 | 2026-04-07 | pending | none | stale |
| metasphere-status-warn-when-metasphere-telegramservice-and-m | 2026-04-08 | pending | none — created today | stale |
| migrate-bean-vm-databasicboldde-to-metasphere-harness-reusin | 2026-04-07 | pending | none | stale |
| migration-port-openclaw-cron-jobs-and-research-agents-to-metasphere-scheduling-system-20260407 | 2026-04-07 | pending | a6809ac (migration: copy persona files, port openclaw cron jobs, native settings.local.json hooks) | **completed** |
| run-ux-review-loop-2-after-fixes-20260406 | 2026-04-07 | pending | none | stale |
| seed-spot-identity-files--kill-stale-claude-20260406 | 2026-04-07 | pending | 6084d2c partial; identity-seeding portion never landed | stale |
| set-up-recurse-project-e2e-with-memory-files-for-morning-tes | 2026-04-08 | pending | fbfa7b2 (recurse: seed project memory files SOUL/MISSION/HEARTBEAT/LEARNINGS/persona-index) | **completed** |
| smoke-after-swap | 2026-04-08 | pending | none — test marker | stale |
| task-20260406-8348 (Build Metasphere MVP) | 2026-04-07 | pending | b096a5d (Python rewrite v0) + entire metasphere/ subpackage tree | **completed** |
| tune-metasphere-context-truncation-budgets-20260406 | 2026-04-07 | pending | none | stale |
| watchdog-detect-pasted-but-unsubmitted-tmux-state-20260406 | 2026-04-07 | pending | ecc1710 (Bulletproof tmux submit with paste-placeholder watchdog) | **completed** |
| generalize-/spot-to-/instances-multi-host.md | 2026-04-07 | pending | malformed slug — pre-fix slugify bug stored title fragments as subdirs | **orphaned** |
| telegram-/newproject-creates-group.md | 2026-04-07 | pending | malformed slug | **orphaned** |
| wire-/btw-to-inject-without-forcing-turn.md | 2026-04-07 | pending | malformed slug | **orphaned** |
| projects-create--/group-create-with-agent-assignment.md | 2026-04-07 | pending | malformed slug | **orphaned** |
| installsh-detect-openclaw-legacy-migrate-to-native-/metasphere-files-not-treat-openclaw-as-authoritative.md | 2026-04-07 | pending | malformed slug | **orphaned** |

## Root cause (preview — full RCA in T3)

The spawn lifecycle (`metasphere.agents.spawn_ephemeral` and the bash
`scripts/metasphere-spawn`) writes a free-form `task` description into
the agent's identity dir but **never creates a corresponding
`.tasks/active/<slug>.md` file** and **never records a `task_id`
linking the agent back to a task**. So when an ephemeral agent finishes
its work, neither the agent nor the posthook has any handle to call
`metasphere task <slug> archive`. Closure depends on the agent
remembering to do it manually — and agents don't.

Fix: spawn now creates the task file in scope and writes
`agent_dir/task_id`. Posthook reads `task_id` on every Stop tick and
auto-archives when the agent's status indicates clean completion.
