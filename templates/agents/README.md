# templates/agents/

Per-role spec directories. Each `<role>/` owns the full persona stack
that gets copied into a persistent agent's home when `metasphere agent
seed --spec <role> @<agent>` runs.

## Per-role files

```
templates/agents/<role>/
  config.md     # frontmatter: name, role, sandbox, persistent, auto_memory, triggers
  SOUL.md       # personality, voice, operating rules
  MISSION.md    # default mission template (with {{variable}} placeholders)
  AGENTS.md     # runtime guidelines / operating contract
```

Not every role ships all four files. `seed_agent()` copies what's
present in the dir and skips the rest — so a role with `config.md` +
`AGENTS.md` only (no persona stack) still seeds cleanly.

## Shipped roles

| Role         | SOUL/MISSION? | Notes |
| ------------ | ------------- | ----- |
| `eng`        | yes           | Focused implementer; ephemeral or persistent. |
| `lead`       | yes           | Team lead / planner; routes work to eng + critic. |
| `critic`     | yes           | Review gate; reads code/PRs, never writes. |
| `researcher` | yes           | Deep research, synthesis, structured output. |
| `explorer`   | yes           | Scheduled monitoring / anomaly detection. |
| `designer`   | no            | Interaction-design role; project-scoped, voice set per-project. |
| `orchestrator` | no          | Top-level coordinator; persona seeded by `install.sh` heredoc. |

## Variable substitution

Persona files may reference `{{agent_id}}`, `{{project_name}}`,
`{{project_goal}}`, `{{scope}}`, `{{spec_name}}`, `{{role}}`,
`{{timestamp}}`. The substitution accepts both `{{var}}` and
`{{ var }}` (with whitespace). See
`metasphere/specs.py::_substitute`.

## Adding a new role

1. Create `templates/agents/<role>/config.md` with frontmatter:
   ```
   ---
   name: <role>          # convention: same as dir name for shipped roles
   role: <role>
   description: <one-line role summary>
   sandbox: scoped       # or readonly / none
   persistent: true      # false for one-shot-only roles
   auto_memory: true     # false to suppress the heartbeat Memory Context (FTS) block
   ---
   ```
2. Add `SOUL.md` and/or `MISSION.md` if the role ships a default
   persona stack. Use `{{ ... }}` placeholders for per-spawn fields.
3. Add `AGENTS.md` with the operating contract.
4. `list_specs()` picks the new role up via directory scan — no
   Python edit needed.

## User overrides

Operators can shadow shipped roles or define custom ones by placing
a directory at one of:

1. `~/.metasphere/templates/agents/<role>/` — canonical user override.
2. `~/.metasphere/specs/<custom>/` — legacy user override path; still
   searched but deprecated.

First-match-wins per role name. The `name:` and `role:` fields can
differ in user overrides (e.g. a custom spec named `my-eng` with
`role: eng` will inherit the `eng` AGENTS.md fallback if its own dir
doesn't ship one).
