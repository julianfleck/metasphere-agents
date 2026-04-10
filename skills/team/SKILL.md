---
name: team
description: Invoke agent teams — review, research, implement, plan, or assemble a full team
---

You have been invoked via the `/team` slash command. The user typed:

```
$ARGUMENTS
```

## Routing

Parse `$ARGUMENTS` as `<action> [args...]`:

- **`review [scope]`** — invoke a reviewer agent on the current diff or specified scope
- **`research "<topic>"`** — invoke a researcher agent with a brief
- **`implement "<task>"`** — invoke an implementer agent with a task description
- **`plan "<goal>"`** — invoke a planner agent with a goal
- **`monitor "<target>"`** — invoke a monitor agent on a target
- **`assemble <project> [spec1 spec2 ...]`** — seed + wake multiple agents for a project
- **`status`** — show all team members and their status
- **`specs`** — list available agent specs
- Empty or `help` — show this help

## How to invoke a single agent

For `review`, `research`, `implement`, `plan`, `monitor`:

1. Determine the spec name from the action (e.g. `review` -> spec `reviewer`)
2. Check if a matching persistent agent already exists for the current project:
   - Look at the project's members list in `project.json`
   - Or check `~/.metasphere/agents/` for agents with a matching `spec` file
3. If no matching agent exists, seed one:
   ```bash
   metasphere agent seed --spec <spec-name> @<project>-<spec> --project <project-name>
   ```
4. Wake the agent if not already alive:
   ```bash
   metasphere agent wake @<agent-name>
   ```
5. Create a metasphere task in the project scope:
   ```bash
   tasks new "<task description>" !normal
   ```
6. Send the task to the agent via messages:
   ```bash
   messages send @<agent-name> !task "<task description with context>"
   ```
7. Report to the user what you did:
   - Which agent was invoked
   - What task was created
   - How to check on progress: `tmux attach -t metasphere-<name>`

## How to assemble a full team

For `assemble <project> [spec1 spec2 ...]`:

1. If specs are listed, seed + wake each one for the project
2. If no specs listed, use a default team: `reviewer researcher implementer planner`
3. For each spec:
   - `metasphere agent seed --spec <spec> @<project>-<spec> --project <project>`
   - Add as project member if not already: `metasphere project member add @<agent> --role <role> --persistent`
   - `metasphere agent wake @<agent>`
4. Report: which agents are now alive, what the team looks like

## How to show status

For `status`:

1. Find the current project (walk up from CWD looking for `.metasphere/project.json`)
2. List all project members with their status (alive/dormant, spec, last activity)
3. If no project context, list all persistent agents system-wide

## Context

You have access to these commands:
- `metasphere agent specs` — list available specs
- `metasphere agent seed --spec <name> @agent [--project <name>]` — seed from spec
- `metasphere agent wake @agent` — wake persistent agent in tmux
- `metasphere agent list` — list persistent agents
- `tasks new "title" !priority` — create task in current scope
- `messages send @agent !task "description"` — send task to agent
- `metasphere project list` — list projects
- `metasphere project show <name>` — show project details

Always create a metasphere task when invoking an agent — the task is the
coordination artifact that tracks the work across sessions.
