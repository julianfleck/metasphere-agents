# .tasks/

Fractal task store for this scope. Same upward-visibility model as `.messages/`: an agent here sees tasks in this directory plus all parent directories.

## Layout

```
.tasks/
├── active/       # in-progress + pending tasks
└── completed/    # finished tasks (kept for history)
```

One file per task, named `task-<unix_us>-<rand>.task`.

## File format

```
title: short title
priority: !high
status: in-progress
assigned: @orchestrator
created: 2026-04-07T05:00:00Z
parent: <task-id or empty>
---
Free-form task description, acceptance criteria, and ongoing notes.
The CLI appends update lines below; the body grows over time.
```

Status transitions: `pending → in-progress → completed` (or `blocked`). Priorities: `!urgent`, `!high`, `!normal`, `!low`. Use the `tasks` CLI (`scripts/tasks`) to write — it handles file naming, updates, and the move from `active/` to `completed/`.

## Two task systems — don't confuse them

| System | Storage | Lifetime | Use for |
|---|---|---|---|
| **metasphere tasks** (this directory) | files, git-versioned | persistent across sessions and crashes | features, bugs, anything that should survive the conversation |
| **Claude Code TaskCreate** (in-memory) | session state | dies with the conversation | breaking down a single turn's work into trackable steps |

If you find yourself adding more than ~5 items to TaskCreate, stop and migrate them here. See `../CLAUDE.md` for the full rule.

## Cross-host

Tasks are **local to a host**. Each metasphere installation has its own task tree. Don't try to coordinate work between hosts through tasks — use Telegram (human-mediated) or CAM (shared learnings). Installations evolve in parallel.
