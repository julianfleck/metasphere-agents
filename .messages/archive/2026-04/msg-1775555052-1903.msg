---
id: msg-1775555052-1903
from: @orchestrator
to: @user
label: !reply
status: unread
scope: /
created: 2026-04-07T09:44:12Z
read_at:
replied_at:
completed_at:
reply_to: msg-1775537975-38355
---

OPENCLAW CANONICAL LAYOUT — research complete (sources: docs.openclaw.ai/concepts/agent-workspace, github.com/openclaw/openclaw, github.com/mergisi/awesome-openclaw-agents).

== Canonical workspace files (~/.openclaw/workspace/ root) ==
Real upstream-shipped or upstream-documented files:
- AGENTS.md      operating instructions, behavioral rules    [user-created]
- SOUL.md        persona, tone, boundaries                   [user-created]
- USER.md        user identity / address preferences         [user-created]
- IDENTITY.md    agent name, vibe, emoji                     [upstream-seeded]
- TOOLS.md       local tools and conventions                 [user-created]
- HEARTBEAT.md   optional checklist for heartbeat runs       [user-created]
- BOOT.md        optional startup checklist                  [user-created]
- BOOTSTRAP.md   one-time first-run ritual                   [upstream-seeded]
- MEMORY.md      curated long-term memory                    [user-created]

== Canonical workspace dirs ==
- memory/        daily logs YYYY-MM-DD.md
- skills/        workspace-specific skills (highest precedence)
- canvas/        canvas UI files

== Outside the workspace, under ~/.openclaw/ ==
- openclaw.json                              global config
- agents/<agentId>/auth-profiles.json        API keys / OAuth
- agents/<agentId>/sessions/                 transcripts
- credentials/                               channel/provider state
- skills/                                    managed (non-workspace) skills

== Cross-reference vs spot's actual install ==
Canonical files PRESENT on spot: AGENTS, SOUL, USER, IDENTITY, TOOLS, HEARTBEAT, MEMORY (✓)
Canonical files MISSING on spot: BOOT.md, BOOTSTRAP.md
Spot user-extras (NOT canonical, NOT migration targets): README.md, BACKLOG.md, links.md, links.yaml
Canonical dirs PRESENT: memory/ (72 daily logs)
Canonical dirs MISSING/elsewhere: workspace/skills (skills are at ~/.openclaw/skills not in workspace), canvas/

Spot has a TON of extra workspace subdirs that are user content, not canonical: areas, research, references, docs, projects, recurse, repos, logs, scripts, sessions, tasks, www, contacts, data, package, poc, node_modules, venv, .venv, .git, .openclaw. None of these are openclaw-shipped — they're the user's working files.

== Top-level ~/.openclaw/ on spot — beyond canonical ==
Real openclaw-installer artifacts (treat as migration targets if relevant):
agents/, skills/, credentials/, openclaw.json, browser/, canvas/, completions/, config-backup/, cron/, delivery-queue/, devices/, display/, exec-approvals.json, extensions/, identity/, logs/, media/, memory/, scripts/, subagents/, telegram/, update-check.json

== Cron / scheduled agents (the other migration concern) ==
Found ~/.openclaw/cron/jobs.json on spot. 50+ scheduled jobs defined, ~17 currently enabled. Notable enabled ones:
- "Morning briefing"               cron 0 10 * * 1-5 (Europe/Berlin)
- spot:autonomous-exploration      every 2h
- research-monitor:brand-mentions  daily 5am
- research-monitor:memory-architectures, retrieval-architectures, agentic-reasoning, evaluation-governance, divergence-engines, ephemeral-interfaces — weekly Mon/Fri 5am
- research-monitor:residency-programs, job-opportunities, accelerator-programs — daily 5am
- rage-changelog-update             weekdays 9:30am
- polymarket:trading-run            every 2h
- polymarket:quick-scan             every 30min
- polymarket:daily-summary          9am + 9pm
Agent IDs configured under ~/.openclaw/agents/: main, night, research-gather, research-synthesize, coding-integration, coding-simple
Each cron job in jobs.json carries a payload.message (multi-line prompt) that's the actual instruction the agent runs when triggered.

== Conclusion: real migration targets ==
1. Workspace persona files (9 canonical .md files)
2. ~/.openclaw/workspace/memory/*.md (daily logs — 72 on spot)
3. ~/.openclaw/openclaw.json secrets/config
4. ~/.openclaw/agents/<id>/{auth-profiles.json,sessions/}
5. ~/.openclaw/credentials/
6. ~/.openclaw/skills/ (10+ skills on spot — cam, agent-development, frontend-design, hook-development, linear-cli, linkedin-cli, log-work, etc.)
7. ~/.openclaw/cron/jobs.json (50 jobs to port to metasphere schedule)

Spot is NOT "completely blank" — its memory is rich and intact under ~/.openclaw/. Metasphere just has no view into it. The install.sh refactor + cron migration tasks now have enough info to design the bundle. Sources cited: docs.openclaw.ai, github.com/openclaw/openclaw.
