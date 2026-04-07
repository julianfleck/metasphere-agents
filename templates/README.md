# templates/

Boilerplate dropped into freshly-spawned agents.

## `agent-harness.md`

Bootstrap document copied into a new agent's identity directory (`~/.metasphere/agents/@<name>/`) when `metasphere-spawn` creates it. Contains:

- The agent's role and scope as the first thing it reads
- The same operational primer as the project `CLAUDE.md` (messages CLI, tasks CLI, completion protocol, message labels, task priorities)
- A pointer back to the parent agent
- The "Use the harness, evolve the harness" reminder

If you change how spawned agents bootstrap (e.g. add a new mandatory step at startup), edit this file. Existing agents won't be re-templated, but every new spawn picks it up.
