# Agent home layout

What files live in `~/.metasphere/agents/@<name>/` and what each one
does. Read this when setting up a multi-agent harness for the first
time, or when your orchestrator is missing files and you want to
understand what's expected.

For the per-role *template* layout (the files that get copied into an
agent's home when you seed), see
[`templates/agents/README.md`](../templates/agents/README.md).

## Per-agent layout

A persistent agent's home looks like this:

```
~/.metasphere/agents/@<name>/
  AGENTS.md       # runtime operating contract (REQUIRED)
  SOUL.md         # voice, personality, operating rules
  MISSION.md      # what this agent is for (filled-in template)
  IDENTITY.md     # name, handles, public-facing presence (orchestrator only)
  USER.md         # operator profile + relationship rules (orchestrator only)
  TOOLS.md        # tool / CLI cheat-sheet (orchestrator only)
  LEARNINGS.md    # what this agent learned over time
  MEMORY.md       # what this agent knows: facts, configs, refs
  HEARTBEAT.md    # heartbeat-rendered context block (auto-generated)
  persona-index.md # index of voices / characters this agent maintains

  spawned_at      # ISO timestamp of cold-start
  last_active     # ISO timestamp updated each turn
  status          # current state: persistent / dormant / ephemeral / exited
  activity.json   # session metadata

  artifacts/      # this agent's outputs, briefs, dossiers (writable)
  daily/          # YYYY-MM-DD.md daily logs (append-only)
  state/          # ephemeral runtime state (agent-internal)
  brain/          # optional: this agent's private working memory

  .last_active.lock  # sidecar locks for atomic writes
  .status.lock
  .spawned_at.lock
  ...
```

Ephemeral one-shot agents (spawned via `metasphere agent spawn`) get a
slimmer subset: usually `AGENTS.md`, `SOUL.md`, `MISSION.md`,
`LEARNINGS.md`, plus a few lock files and `output.log`. They don't
need `IDENTITY.md` / `USER.md` / `TOOLS.md` — those are
orchestrator-scope.

## What each file holds

### Required

**`AGENTS.md`** — The operating contract. This is the file Claude
Code's session-start hook reads to populate the agent's standing
instructions. It covers: voice, triage rules, message-passing
discipline, autonomy mode, lifecycle, error handling. **If your
orchestrator doesn't have `AGENTS.md`, the agent has no standing
instructions and will behave like a generic Claude Code session.**

For shipped roles (`eng`, `lead`, `critic`, `designer`, `researcher`,
`explorer`), this is sourced from `templates/agents/<role>/AGENTS.md`
and copied into the agent's home by `metasphere agent seed`.

For `orchestrator`, this file is more bespoke — the orchestrator is
the operator-facing coordinator and its AGENTS.md typically encodes
project-specific voice, triage rules, and routing discipline. Start
from the existing `templates/install/CLAUDE.md` as a system overview
and write your own `AGENTS.md` describing how your orchestrator
should behave.

### Persona stack

**`SOUL.md`** — Voice and personality. Short (a few hundred to a few
thousand words). Sets the register: how the agent speaks, what kinds
of phrases to avoid, what mood it operates in. Read it once, internalize.

**`MISSION.md`** — What this agent is *for*. The purpose-statement.
For shipped roles this comes from `templates/agents/<role>/MISSION.md`
with `{{variable}}` placeholders substituted at seed time. For
custom orchestrators, write it freeform.

`designer` is the documented exception — it ships without `SOUL.md`
or `MISSION.md` because its voice is project-scoped, set per project
rather than baked into the spec. The seeder copies just `AGENTS.md`
(and `config.md`) for designer.

**`persona-index.md`** — Optional. If the agent operates under
multiple voices (e.g. an external alias + internal identity), index
them here. Most agents don't need this.

### Identity (orchestrator only)

**`IDENTITY.md`** — Name, handles, public-facing presence.
Orchestrator-scope because the orchestrator is the operator-visible
agent. Other agents inherit identity from their role.

**`USER.md`** — Profile of the operator the orchestrator works with:
preferences, working style, what to escalate vs suppress, what
language register to use. Critical for orchestrators because they're
the human-interface layer.

**`TOOLS.md`** — Cheat-sheet of CLIs / tools the agent should know.
Usually project-specific.

### Memory

**`LEARNINGS.md`** — Append-only log of insights, debugging
discoveries, lessons learned the hard way. Agents read this when
they hit familiar-looking problems.

**`MEMORY.md`** — Facts and references. Configs, system topology,
external service URLs, ongoing project state. Read at session start;
write when you learn something durable.

Both files get rendered into the heartbeat context block via the
per-project memory hook — see
[`PROJECTS.md`](./PROJECTS.md) for how project-scoped memory differs
from agent-scoped memory.

### Auto-generated

**`HEARTBEAT.md`** — The per-turn context block injected into the
session. Rendered by the heartbeat hook; do not edit by hand. Reflects
current state from AGENTS / SOUL / MISSION / LEARNINGS / MEMORY /
recent telegram / unread messages / active tasks.

**`activity.json`**, **`spawned_at`**, **`last_active`**, **`status`**
— Lifecycle metadata maintained by the harness. The lock files
(`.last_active.lock` etc.) exist to make file writes atomic — leave
them alone.

### Workspace

**`artifacts/`** — Where this agent's outputs go. Briefs, dossiers,
reports, planning documents. Other agents can read these. Convention:
`artifacts/YYYY-MM-DD-<slug>.md`.

**`daily/`** — Daily log files, one per day. Append-only. The
orchestrator's daily log is a first-class running narrative; other
agents typically don't keep daily logs.

**`state/`** — Agent-internal runtime state. Don't expect other agents
to read this.

**`brain/`** — Optional working memory. Some agents use this for
in-flight chain-of-thought; others ignore it.

## Minimum viable setup

For a new orchestrator on a fresh instance, you need at minimum:

```
~/.metasphere/agents/@<name>/
  AGENTS.md       # the contract — write this carefully
  SOUL.md         # voice
  MISSION.md      # purpose
  IDENTITY.md     # name + handles
  USER.md         # operator profile
```

The harness will create `LEARNINGS.md`, `MEMORY.md`, `HEARTBEAT.md`,
`artifacts/`, `daily/`, and lifecycle files on first run. You can
seed them all by hand, or let them appear when the agent first
runs.

## How to seed shipped roles

For non-orchestrator agents (`eng`, `lead`, `critic`, `designer`,
`researcher`, `explorer`), use the seeder:

```
metasphere agent seed --spec eng @my-eng
metasphere agent seed --spec critic @my-critic
metasphere agent seed --spec researcher @my-researcher
```

This copies `templates/agents/<role>/{config,SOUL,MISSION,AGENTS}.md`
into `~/.metasphere/agents/@<name>/` with `{{variable}}` substitution.
The `--force` flag overwrites existing files (useful when updating an
agent's persona stack after a template change).

For orchestrators, `install.sh` seeds the initial home directory and
writes a starter `AGENTS.md` heredoc. If your orchestrator is missing
`AGENTS.md`, you can:

1. Re-run `install.sh` (it'll re-seed missing files).
2. Hand-write `AGENTS.md` from the operator-facing system overview at
   `templates/install/CLAUDE.md` plus the per-role discipline you
   want (triage / telegram bridge / autonomy / etc.).
3. Copy a working orchestrator's `AGENTS.md` and edit for your
   instance — the file is heavily idiosyncratic per orchestrator.

## Persistent vs ephemeral

| Aspect | Persistent | Ephemeral |
|---|---|---|
| Home dir | Survives between sessions | Created on spawn, may be reaped |
| Files | Full set above | AGENTS.md + SOUL.md + MISSION.md + LEARNINGS.md |
| Memory | LEARNINGS / MEMORY accumulate | LEARNINGS migrates to project on exit |
| Tmux session | Yes, kept alive | No, headless one-shot |
| Wake | `metasphere agent wake @<name>` | N/A (spawn-and-done) |
| Use case | Long-running coordinator, explorer, team member | Single well-scoped task |

The orchestrator is always persistent. Specialist team members
(`eng`, `lead`, `critic`) can be either — usually persistent for an
active project, ephemeral for one-shot tasks.

## Variable substitution

When seeding, these `{{...}}` placeholders are filled in:

- `{{agent_id}}` — bare name (no `@`)
- `{{role}}` — role from config.md
- `{{spec_name}}` — same as role for shipped roles
- `{{project_name}}` — current project context
- `{{project_goal}}` — project's stated goal
- `{{scope}}` — agent's working directory
- `{{timestamp}}` — ISO timestamp at seed

Both `{{var}}` and `{{ var }}` (with whitespace) are accepted. See
`metasphere/specs.py::_substitute`.

## Common gotchas

- **Missing AGENTS.md**: agent runs as a generic Claude Code session
  with no standing instructions. Symptom: agent behaves "off-character"
  or asks for clarification on things it should know.
- **Stale persona stack**: edits to `templates/agents/<role>/` don't
  propagate to live agents until you re-seed with `--force`.
- **AGENTS.md too long**: there's a session-start context budget. If
  your AGENTS.md is 30k+ tokens, the agent might hit truncation. Aim
  for under 20k for orchestrators; under 5k for specialist roles.
- **Persona-file edits during a session**: a running agent's session
  has already loaded its AGENTS.md / SOUL.md. Edits take effect on
  the *next* cold-start, not immediately. Use `metasphere session
  restart` to force a reload.
- **Lock files in version control**: never commit `.lock` sidecars
  or per-instance `activity.json`. They're runtime state, not code.

## Reference: a working orchestrator's home

For a concrete reference, look at:

```
templates/install/CLAUDE.md           # operator-facing system overview
templates/agents/orchestrator/        # minimal seed for orchestrator role
```

And consider the file shapes used by shipped specialist roles in
`templates/agents/<role>/` — those are the canonical reference for
what persistent-agent persona stacks look like in practice.
