# Metasphere

This is the shared operating manual for every agent in this
metasphere install — a multi-agent harness on Claude Code. It
covers what's common to all of you: how messaging, tasks,
delegation, projects, and teams work, and the `metasphere` CLI
that drives them. All state lives under `~/.metasphere/`.

**Your specific role is not in this file.** Who you are, the scope
you run at, the surface you speak on, and your runtime rules —
heartbeat etiquette, response style, completion protocol, memory
hygiene — live in your own identity files under
`~/.metasphere/agents/$METASPHERE_AGENT_ID/` (SOUL, USER, MISSION,
AGENTS), read at session start alongside this file. When this
manual and your identity files both speak to something, your
identity files win: they are role-specific, this is the baseline.

## How agents and humans connect

The gateway daemon (a systemd user service) is the bridge between
outside surfaces (Telegram, Slack) and agent REPLs. It polls each
configured surface, routes an inbound message to the agent that
*owns* that surface, and pipes it into that agent's tmux REPL. Each
agent's turn-end output is relayed back out to its surface by the
per-turn Stop hook (with quiet-tick suppression when there's
nothing worth sending). A human can always
`tmux attach -t metasphere-<agent>` to watch a REPL directly —
that's the back door, not the default path.

**Surfaces are owned, not shared.** You only speak on the
surface(s) configured for your agent. If a conversation shows up in
your context that belongs to another agent's surface, it is not
yours to answer — leave it. Agent-to-agent coordination never goes
over a human surface; it uses the message system below.

## Delegation

Coordinating agents (the orchestrator, project leads) push
state-writing work — code edits, tests, commits, migrations,
deploys, anything that runs longer than ~30s — to a child agent
rather than doing it in their own turn. Whether that discipline
applies to you is set by your role's `AGENTS.md`; an implementing
agent does the work directly. When you *do* delegate, two flavors:

- **Ephemeral** (`metasphere agent spawn @name /scope/ "task"`):
  one well-scoped task, agent exits on `!done`. Use for mechanical
  state-writes — ship a fix, run a migration, open a PR.
- **Persistent** (`metasphere agent wake @name`): long-lived
  collaborator with their own SOUL/MISSION/LEARNINGS/HEARTBEAT and
  tmux session. Use when work spans multiple turns and you'll want
  to course-correct mid-flight.

Every spawn carries a three-field contract:

- **Authority**: what the child *may* do (scope boundary, allowed
  tools, allowed side-effects). Privilege attenuation — they get
  less than you have, not the same.
- **Responsibility**: what they *must* produce (concrete nouns,
  not verbs — "ships commit SHA on main", not "works on the fix").
- **Accountability**: how *you* will verify on `!done` (a concrete,
  re-runnable check you'll actually execute).

If you can't write all three, the task is too subjective — decompose
further before spawning. Contract-first delegation comes from
Intelligent Delegation (DeepMind, arxiv 2602.11865); the discipline
detail lives in your AGENTS.md.

The child reports back via `metasphere msg send @.. !done "..."`
with attestation (commit SHAs, test pass counts, file paths, IDs).
Re-run the Accountability check before forwarding `!done` upstream
or closing the loop. Don't act as an unthinking router.

Coordination uses the metasphere message system — NOT a human
surface. Telegram and Slack are for talking to humans; agents talk
to each other via `metasphere msg send` (file-based, under
`.messages/inbox/` and `.messages/outbox/` per scope). Each agent
runs in its own tmux session.

## Projects

A project is a unit of work with its own scope, members, and
state. Lives at `~/.metasphere/projects/<project-name>/`:

```
projects/<name>/
├── CLAUDE.md       # Project descriptor (goal, members, status)
├── project.json    # Registry metadata
├── .tasks/         # Active and completed tasks
├── .messages/      # Per-project inbox/outbox
├── .changelog/     # Project-scoped changes
├── .learnings/     # Project-scoped insights
└── shared/         # Cross-agent artifacts (visible to teammates)
```

Manage projects from your REPL:

```bash
metasphere project new <name> --goal "..." --member @x:role[:persistent]
metasphere project list
metasphere project show [name]
metasphere project wake [name]                 # bring up project lead
metasphere project chat <name> "message"       # send to project telegram topic
metasphere project member add <name> @agent --role R [--persistent]
metasphere project for [path]                  # print enclosing project
```

Slash-command form is also available: `/project new|list|show|wake|chat`.

Each project's lead persona lives at `~/.metasphere/agents/@<project>-lead/`.
Wake the lead when you need work in that project's scope — they
own decomposition, member coordination, and verification within
their project. The lead inherits the same delegation discipline
as you, scoped to their project.

## Per-project memory

Each project has two team-shared memory files at
`~/.metasphere/projects/<name>/`:

- **`LEARNINGS.md`** — what the team learned (incidents, lessons,
  debugging insights).
- **`MEMORY.md`** — what the team knows (facts, configs, references,
  ongoing state).

These files are **the primary memory store** for everyone on the
team. Entries are dated `YYYY-MM-DD: title`. The per-turn context
hook renders a recency window (newest entries first, within a byte
budget) into each member's context block under a `## Project:
<name>` heading, with a footer that cites the absolute on-disk path:

```
_(N more entries omitted by recency. Full file: /home/<user>/.metasphere/projects/<name>/LEARNINGS.md — Read or grep for older.)_
```

When an agent doesn't have an answer in their capsule and the
question matters, they `Read` or `grep` the file directly using the
path from the footer. Per-role `AGENTS.md` nudges them toward this
pattern but explicitly NOT to reflex-grep on every project query
(would dilute reasoning). The capsule is the recency lens; the full
files are the primary store.

### How an agent's project is resolved

The capsule is injected when the agent resolves to one or more
projects. Resolution chain (highest priority first):

1. **`MISSION.md` frontmatter** — `project: <name>` (scalar) or
   `projects: [a, b]` (list). Explicit override; supports
   multi-project.
2. **`~/.metasphere/teams.yaml`** — central agent→projects roster.
   Covers agents whose name doesn't follow the `<project>-<role>`
   convention (e.g. `@spot`, `@orchestrator`). Multi-project
   natively. Schema at the top of the file; edits land within one
   turn.
3. **Path-nested location** — agents living at
   `~/.metasphere/projects/<P>/agents/@<id>/` resolve to `<P>`.
4. No capsule otherwise.

Name-prefix string matching (`@<project>-<role>` → `<project>` by
dash-split) was used briefly but found brittle (e.g.
`@polymarket-agents-research` first-dash-splits to `polymarket`
which doesn't match project `polymarket-agents`). Replaced by
`teams.yaml`.

### Auto-memory layer

Claude Code's auto-memory at `~/.claude/projects/...` is
cross-conversation residue — secondary to the project files. Useful
for recent-context recall, not authoritative for project facts.

### Live agents and template updates

When a per-role `AGENTS.md` template is updated, existing live
persistent agents don't pick up the new content until re-seeded:

```bash
metasphere agent seed --spec <spec-name> --force
```

Without `--force`, the seeder refuses to overwrite an existing
file. Run this manually for each agent after updating their role's
template; daemons don't auto-re-seed.

## Teams

For non-trivial multi-agent work, wake a team rather than spawning
single ephemerals. Standard shape: a lead + eng + critic, all
persistent, sharing a project scope. The lead decomposes; eng
implements (via further delegation); critic reviews and pushes
back before merge.

Teams are spawned via slash commands in Claude Code (no
`metasphere team` CLI subcommand exists yet):

```
/team review     # code-review team for the current branch
/team research   # research team for an open question
/team implement  # implementation team for a planned feature
/team plan       # planning team for an undefined initiative
/team monitor    # monitoring/exploration agent on a target
/team assemble <project> [specs...]   # seed + wake a full team
/team status     # show team members and their status
/team specs      # list available agent specs
```

Team members live at `~/.metasphere/agents/@<role>-<project>/`.
Manage them like any other persistent agents: `msg send` to
delegate, course-correct mid-flight, verify on `!done`.

## Operational context

| Field | Value |
|---|---|
| Runtime root | `~/.metasphere/` |
| Default agent | `@orchestrator` (persistent, runs at root scope) |
| Identity dir | `~/.metasphere/agents/$METASPHERE_AGENT_ID/` |
| Task / message data | `~/.metasphere/projects/<project>/.tasks/`, `.messages/` |
| Hooks | `~/.metasphere/.claude/settings.local.json` |
| Gateway daemon | systemd user service, polls Telegram + manages tmux |

Environment variables:

```bash
METASPHERE_AGENT_ID      # Current agent (default: @user)
METASPHERE_SCOPE         # Current scope directory
METASPHERE_PROJECT_ROOT  # Project root (fractal scoping anchor)
METASPHERE_DIR           # Runtime directory (default: ~/.metasphere)
```

## CLI reference

The `metasphere` command is the single entry point. Subcommands:

```bash
# ── Messages ─────────────────────────────────────────────
metasphere msg                              # Show unread
metasphere msg all                          # Show all including read
metasphere msg send @target !label "msg"    # Send to target
metasphere msg reply <msg-id> "response"    # Reply
metasphere msg done <msg-id> "note"         # Mark complete

# ── Tasks ────────────────────────────────────────────────
metasphere task                             # Show active
metasphere task new "title" !priority       # Create task
metasphere task start <task-id>             # Assign to self
metasphere task update <task-id> "note"     # Add progress
metasphere task done <task-id> "summary"    # Complete

# ── Agents ───────────────────────────────────────────────
metasphere agent spawn @name /scope/ "task"   # One-shot agent
metasphere agent wake @name                   # Persistent collaborator
metasphere agents                             # List all agents

# ── Messaging (cross-surface) ────────────────────────────
metasphere message send "msg"                 # Send on the active surface (auto)
metasphere message send "msg" --to <name>     # To addressbook contact (surface-aware)
metasphere message send "msg" --surface <id>  # Pin a surface (telegram, slack-…)

# ── Telegram (surface-specific; legacy form still works) ──
metasphere telegram send "message"            # Send to default chat
metasphere telegram send "@<name>" "msg"      # Send to addressbook contact
metasphere telegram send "msg" --to <name>    # Equivalent long form
metasphere telegram send "msg" --chat-id N    # Send to arbitrary chat
metasphere telegram send-document path.pdf    # Upload a file

# ── System ───────────────────────────────────────────────
metasphere status                     # Full system overview
metasphere gateway status             # Gateway + session health
metasphere schedule list              # Cron jobs
metasphere update                     # Pull latest + restart
metasphere session restart            # Restart orchestrator REPL
```

## Directory structure

```
~/.metasphere/
├── CLAUDE.md                # This file (shared operating manual)
├── ADDRESSBOOK.yaml         # Named contacts for `telegram send "@<name>"`
├── .claude/                 # Claude-Code settings (hooks, permissions)
├── agents/                  # One subdir per agent
│   └── @<id>/
│       ├── SOUL.md          # Voice — read at session start
│       ├── USER.md          # Who you are — read at session start
│       ├── MISSION.md       # This agent's role
│       ├── AGENTS.md        # Agent runtime guidelines (per type)
│       ├── HEARTBEAT.md     # Current state + stable rules
│       ├── LEARNINGS.md     # Accumulated insights
│       ├── MEMORY.md        # Curated long-term memory
│       └── persona-index.md # Index of which file to read when
├── projects/                # Per-project data
│   └── <project>/
│       ├── CLAUDE.md        # Project descriptor (goal, members)
│       ├── .tasks/          # Active and completed tasks
│       ├── .messages/       # Inbox / outbox
│       └── shared/          # Cross-agent artifacts
└── state/                   # Daemon state (don't edit by hand)
```

Fractal scoping: every project dir can have its own `.tasks/` and
`.messages/`. Agents see their scope plus parent scopes (upward
visibility).

## Slash commands (in Claude Code)

```bash
/project new|list|show|wake|chat                     # Manage projects
/session restart|status                              # Restart orchestrator REPL
/team review|research|implement|plan|monitor         # Invoke a single-role agent
/team assemble <project> [specs...]                  # Seed + wake multiple agents
/team status|specs                                   # Inspect team members / available specs
```

## Message labels

| Label | Purpose |
|---|---|
| `!task` | Task assignment |
| `!urgent` | Needs immediate attention |
| `!info` | Informational update |
| `!query` | Asking for information |
| `!done` | Task completion |
| `!reply` | Reply to previous message |

## Task priorities

| Priority | Meaning |
|---|---|
| `!urgent` | Critical, immediate |
| `!high` | Important, prioritize |
| `!normal` | Standard (default) |
| `!low` | When time permits |

## Status values

```bash
# Agent status (in status file)
spawned: description    # Just created
working: description    # Active work
waiting: description    # Blocked on input
complete: description   # Task finished

# Message status (in message file)
unread → read → replied → completed

# Task status (in task file)
pending → in-progress → completed
```

## Legacy harness migration

If your host previously ran an older agent harness (e.g.
openclaw), `install.sh` can register
the prior workspace as a *live legacy context source* rather than
copying files out of it. When that registration is in place, the
per-turn context hook may inject persona files (SOUL, IDENTITY,
USER, TOOLS, AGENTS, MEMORY) from the legacy workspace, point
CAM/FTS at the legacy memory store in place, and symlink legacy
skills into `~/.metasphere/skills/`. Tokens and channel config
(e.g. the Telegram bot token) are migrated into
`~/.metasphere/config/` at install time.

If a legacy workspace is registered:

1. Edits to legacy workspace files take effect on the next turn.
2. Don't duplicate legacy data into `~/.metasphere/` — keep one
   source of truth.
3. Treat the legacy workspace as authoritative for persona/identity.
4. Detection happens at install time. Pointer files under
   `~/.metasphere/config/` indicate a registered workspace.

On a fresh install with no legacy workspace, per-turn context comes
only from `~/.metasphere/agents/$METASPHERE_AGENT_ID/` and the
fractal `.messages/` + `.tasks/` directories.
