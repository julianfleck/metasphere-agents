# templates/

Boilerplate copied into a freshly-spawned agent or installed into
`~/.metasphere/` on first install. Three sibling surfaces, each
serving a different stage of the lifecycle.

## `agent-harness.md`

Render template for **ephemeral one-shot agents** spawned via
`metasphere agent spawn` (the headless `claude -p` path). Contains:

- The agent's role and scope as the first thing it reads
- The same operational primer as the project `CLAUDE.md` (messages
  CLI, tasks CLI, completion protocol, message labels, task
  priorities)
- A pointer back to the parent agent
- The "Use the harness, evolve the harness" reminder

If you change how spawned ephemerals bootstrap (e.g. add a new
mandatory step at startup), edit this file. Existing agents won't
be re-templated, but every new spawn picks it up.

## `agents/<role>/`

Per-**role** spec directories. Each role owns the full persona stack —
`SOUL.md`, `MISSION.md`, `AGENTS.md`, `config.md` — in a single dir,
copied into a persistent agent's home (`~/.metasphere/agents/<id>/`)
when `metasphere agent seed --spec <role> @<agent>` runs. Current set:

- `critic/` — review-gate agents
- `designer/` — interaction-designer agents (config + AGENTS.md only;
  no SOUL/MISSION ships — designer is a project-scoped role)
- `eng/` — implementation agents
- `explorer/` — autonomous exploration agents
- `lead/` — team-lead agents
- `researcher/` — research / investigation agents
- `orchestrator/` — orchestrator personae (config + AGENTS.md only;
  persona seeded by `install.sh` heredoc, not via `seed_agent`)

See `templates/agents/README.md` for the per-role contract and
`metasphere/specs.py` for the resolver. `metasphere update`'s
drift-check resolves an agent's role from its config sidecar and
matches against `templates/agents/<role>/`.

Adding a new role:
1. Create `templates/agents/<role>/` with `config.md` (frontmatter
   declares `name:`, `role:`, `sandbox:`) plus whichever of
   `SOUL.md` / `MISSION.md` / `AGENTS.md` apply. `list_specs()`
   discovers it via directory scan — no Python edit.
2. Convention: `name:` and `role:` are the same string for shipped
   roles, both equal to the directory name. User overrides under
   `~/.metasphere/templates/agents/<role>/` (canonical) or the
   legacy `~/.metasphere/specs/<custom>/` (deprecated, still
   searched) can decouple `name:` from `role:`.

## `install/`

Installed into `~/.metasphere/` once at `install.sh` first-run.
Files here ship to every fresh host. Current contents:

- `CLAUDE.md` — the user-facing manual that lands in
  `~/.metasphere/CLAUDE.md` (harness etiquette, agent-runtime
  surface).
- `ADDRESSBOOK.yaml.template` — bootstrap stub for the local
  agent/user/project address book.
- `projects/CLAUDE.md.template` + `projects/USER.md.template` —
  rendered into each new project's home by
  `metasphere project init` (see `metasphere/specs.py:200` and
  `metasphere/project.py`).

Edits here only affect new installs (or new projects, for the
`projects/` subtree). Existing hosts keep whatever lives at
`~/.metasphere/CLAUDE.md` already; re-installs do not clobber
operator edits.
