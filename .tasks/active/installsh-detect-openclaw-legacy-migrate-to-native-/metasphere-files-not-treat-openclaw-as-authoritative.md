---
id: installsh-detect-openclaw-legacy-migrate-to-native-/metasphere-files-not-treat-openclaw-as-authoritative
title: install.sh: detect openclaw legacy, MIGRATE to native ~/.metasphere files (not treat openclaw as authoritative)
priority: !high
status: pending
scope: /
created: 2026-04-07T09:33:44Z
created_by: @orchestrator
assigned_to:
started_at:
completed_at:
---

# install.sh: detect openclaw legacy, MIGRATE to native ~/.metasphere files (not treat openclaw as authoritative)

## Description

The current install.sh treats `~/.openclaw/workspace/` as a live, authoritative
source for persona/identity files (SOUL, IDENTITY, USER, TOOLS, AGENTS, MEMORY).
That was the right call when openclaw was still in active use, but it has two
problems now:

1. **Coupling.** Metasphere is supposed to be its own thing. Treating openclaw
   as the source of truth means edits in metasphere never propagate, openclaw
   uninstall breaks metasphere, and the user has to mentally remember which
   files live where.
2. **Bundle assembly is broken.** As of 2026-04-07 the gateway no longer
   inlines persona files into the REPL on startup (rightly — that was a 33KB
   slow-paste disaster). Nothing else loads them either. Result: spot's
   @orchestrator runs with `CLAUDE.md` only and no idea it's named "Spot".
   Confirmed by inspecting `~/.metasphere/agents/@orchestrator/` on spot:
   only `SOUL.md` (121 lines, copied at install) plus `status`, `activity.json`,
   `updated_at`. No `MISSION.md`, `IDENTITY.md`, `USER.md`, `LEARNINGS.md`,
   `HEARTBEAT.md`. The full openclaw set lives at `~/.openclaw/workspace/`
   (AGENTS 17K, MEMORY 4.4K, IDENTITY 919B, USER 3.6K, TOOLS 1.8K, SOUL 5.6K,
   BACKLOG 4K, HEARTBEAT 168B) but the agent never reads them on its own.

## What install.sh should do

### Detection phase
- Check for `~/.openclaw/workspace/` AND `~/.openclaw/openclaw.json`.
- If present → "legacy openclaw host". Otherwise → "fresh install".

### Migration phase (legacy host)
- **Copy, don't symlink** the persona files into native locations:
  - `~/.openclaw/workspace/SOUL.md`     → `~/.metasphere/agents/@orchestrator/SOUL.md`
  - `~/.openclaw/workspace/IDENTITY.md` → `~/.metasphere/agents/@orchestrator/IDENTITY.md`
  - `~/.openclaw/workspace/USER.md`     → `~/.metasphere/agents/@orchestrator/USER.md`
  - `~/.openclaw/workspace/TOOLS.md`    → `~/.metasphere/agents/@orchestrator/TOOLS.md`
  - `~/.openclaw/workspace/AGENTS.md`   → `~/.metasphere/agents/@orchestrator/AGENTS.md`
  - `~/.openclaw/workspace/MEMORY.md`   → `~/.metasphere/agents/@orchestrator/MEMORY.md`
  - `~/.openclaw/workspace/HEARTBEAT.md`→ `~/.metasphere/agents/@orchestrator/HEARTBEAT.md`
- Migrate openclaw secrets/tokens from `~/.openclaw/openclaw.json` →
  `~/.metasphere/config/secrets.env` (already partly done for telegram).
- Leave `~/.openclaw/` intact as backup. The user explicitly asked for this.
- Drop the `~/.metasphere/config/openclaw_workspace` pointer file. It's the
  source of the coupling. Anything that reads it should fall back to native.

### Fresh install
- Write template versions of `SOUL.md`, `MISSION.md`, `IDENTITY.md`, etc. into
  `~/.metasphere/agents/@orchestrator/` from `templates/agent-identity/`.
- These templates need to exist; the orchestrator should pick a name + persona
  on first run, OR install.sh prompts the user.

### Context bundle assembly
- Add a `templates/orchestrator-persona-index.md` referenced from CLAUDE.md
  via `@~/.metasphere/agents/@orchestrator/persona-index.md`. install.sh
  generates a per-host `persona-index.md` with the absolute paths to whatever
  identity files exist. The agent reads it on demand via Claude's @file
  mention handling — no slow tmux paste, no upfront 33KB blob, just lazy
  loading when the agent actually needs the persona.
- Optionally also add a `metasphere-context` line that says "Persona files
  available; see ~/.metasphere/agents/@orchestrator/persona-index.md" so the
  agent is reminded each turn that they exist.

### Settings.local.json
- install.sh should also write `.claude/settings.local.json` with the absolute
  paths to `scripts/metasphere-context` and `scripts/metasphere-posthook` for
  THIS checkout. (See task `installsh-write-claude/settingslocaljson-with-absolute-hook-paths-for-this-checkout`.)
  Bundle that into this work.

### CAM
- Out of scope for this task but related: spot does NOT have CAM installed.
  `which cam` returns nothing. The whole memory layer is missing. install.sh
  should at least detect this and warn, or invoke CAM's installer if present.

## Canonical openclaw layout (verified 2026-04-07 against docs.openclaw.ai)

Real upstream files in workspace root:
- AGENTS.md, SOUL.md, USER.md, IDENTITY.md, TOOLS.md, HEARTBEAT.md,
  BOOT.md, BOOTSTRAP.md, MEMORY.md (9 total)

Real upstream dirs in workspace:
- memory/ (daily YYYY-MM-DD.md), skills/, canvas/

Real upstream things outside workspace:
- ~/.openclaw/openclaw.json
- ~/.openclaw/agents/<id>/auth-profiles.json
- ~/.openclaw/agents/<id>/sessions/
- ~/.openclaw/credentials/
- ~/.openclaw/skills/

Spot extras NOT to migrate (user-specific):
- workspace/{README,BACKLOG,links}.md, workspace/links.yaml
- workspace/{areas,research,references,docs,projects,recurse,repos,
  logs,scripts,sessions,tasks,www,contacts,data,package,poc,
  node_modules,venv,.venv,.git,.openclaw}/

Spot top-level openclaw extras (real installer artifacts, may need
detection but not migration to metasphere unless used):
- browser/, canvas/, completions/, config-backup/, cron/,
  delivery-queue/, devices/, display/, exec-approvals.json,
  extensions/, identity/, logs/, media/, memory/, scripts/,
  subagents/, telegram/, update-check.json

The cron/ directory is the bridge to the cron-migration task — see
`migration-port-openclaw-cron-jobs-and-research-agents-20260407`.

## Acceptance Criteria

- [ ] install.sh detects legacy openclaw host vs fresh install.
- [ ] On legacy host: persona files copied to `~/.metasphere/agents/@orchestrator/`
      without removing `~/.openclaw/`.
- [ ] On fresh install: template persona files created.
- [ ] `persona-index.md` written per-host with absolute paths.
- [ ] CLAUDE.md @-references the index so a fresh REPL knows where to find them.
- [ ] `.claude/settings.local.json` written with this checkout's absolute hook paths.
- [ ] Spot, after re-running install.sh, can answer "who are you" with its
      Spot persona without any host-side context injection.

## Updates

- 2026-04-07T09:33:44Z [@orchestrator] Created task

## Subtasks

<!-- Add subtasks as needed -->

## Notes

