---
name: project
description: Create or work with a metasphere project (member groups, goals, telegram topics)
---

You have been invoked via the `/project` slash command. The user typed:

```
$ARGUMENTS
```

## Routing

- If `$ARGUMENTS` is empty or starts with `new`: run the **new-project wizard** below.
- If `$ARGUMENTS` starts with `list` or `ls`: run `metasphere project list` and format the output.
- If `$ARGUMENTS` starts with `show <name>`: run `metasphere project show <name>`.
- If `$ARGUMENTS` starts with `wake <name>`: run `metasphere project wake <name>` and report which persistent members came up.
- If `$ARGUMENTS` starts with `chat <name> '<msg>'`: run `metasphere project chat <name> '<msg>'`.
- Anything else: print this command's help and the available `metasphere project` subcommands.

## New-project wizard

Walk the user through a new project in natural language. Gather, in order:

1. **Name** (required, kebab-case suggested).
2. **Path on disk** — default `$PWD/<name>`. If the user gives a git URL, treat it as `--repo` and ask where to clone it (default `~/Code/<name>`).
3. **Goal** — one sentence about what the project is trying to achieve.
4. **Members** — which persistent agents belong here? Default: just `@orchestrator`. Offer common roles (`@reviewer-quality`, `@researcher`). For each, ask whether they should be persistent.
5. **Optional links** — GitHub issues URL, Linear team, anything the user mentions.

Then issue these commands in order, **showing each one before running it**:

```bash
metasphere project new <name> \
  --path <path> \
  --goal "<goal>" \
  [--repo <url>] \
  [--member @agent:role[:persistent] ...]

# Confirm by showing the project:
metasphere project show <name>

# If any persistent members were declared:
metasphere project wake <name>
```

After confirmation, tell the user:

- How to enter the project scope: `cd <path>` (the next turn will auto-inject the project header into per-turn context).
- How to chat in the project's telegram topic (if one was auto-created): `metasphere project chat <name> 'message'`.
- That the project lifecycle (add/remove members, show, wake) is all under `metasphere project ...` — no separate tool.

## Notes

- The slash command is a prompt template; the orchestrator's reasoning is the wizard. Feel free to interpret partial input and skip questions the user has already answered.
- If a telegram forum is configured (`~/.metasphere/config/telegram_forum_id` exists), `metasphere project new` will auto-create a forum topic named after the project and stash its id under `telegram_topic` in `project.json`.
- Persistent members get a stub `MISSION.md` auto-written if one doesn't already exist, so `metasphere agent wake @name` will honour them as persistent on the next call.
