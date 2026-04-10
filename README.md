# Metasphere Agents

An autonomous agent harness for Claude Code. Your agent runs 24/7, you talk to it from Telegram — it thinks, works, spawns helpers, and reports back. You can interrupt it mid-thought, redirect it, or just watch it work.

## What it does

- **Always-on agent** — runs in tmux, survives disconnects, restarts itself after crashes. You don't babysit it.
- **Telegram as your interface** — message your agent from your phone. Ask questions, give tasks, check on progress. It responds in real-time.
- **Interruptible** — unlike batch-mode agents, you can send a message while the agent is working and it will see it on its next turn. No more waiting for a long task to finish before you can course-correct.
- **Multi-agent** — break complex work into child agents that run in parallel with sandboxed permissions. They report back when done.
- **Projects with transparent tasks** — every task is a markdown file in the project directory. You can read them, edit them, grep them. Nothing is hidden in a database.
- **Scheduled automation** — cron-style jobs for recurring work (market scans, memory consolidation, research monitors)
- **Agent memory** — persistent memory across sessions via daily logs, learnings files, and searchable memory index

## Installation

```bash
curl -fsSL https://raw.githubusercontent.com/julianfleck/metasphere-agents/main/install.sh | bash
```

Or manually:

```bash
git clone https://github.com/julianfleck/metasphere-agents.git
cd metasphere-agents
./install.sh
```

Requires: Claude Code CLI (authenticated), tmux, Python 3.11+, jq.

## Quick Start

```bash
# Connect your Telegram bot (get a token from @BotFather)
metasphere config telegram <your-bot-token>

# Start the agent
metasphere daemon start

# Check it's running
metasphere status
```

Your agent is now live. Message it on Telegram.

## How it works

### Every turn, the agent receives

Before each turn, the harness injects the agent's current context:
- Unread messages (from you, from other agents, from scheduled jobs)
- Active tasks across all projects
- The agent's persona and voice (from SOUL.md)
- Recent events (who did what, what fired, what completed)
- Relevant memories from past sessions

### After each turn

After the agent responds:
- The response is forwarded to your Telegram chat
- Events are logged to the event stream
- Heartbeat state is updated

This means you always see what the agent is doing without having to SSH in and attach to a terminal.

## Tasks

Every task is a markdown file in the project's `.tasks/active/` directory — a full briefing with title, priority, status, owner, acceptance criteria, and a running log of updates. When a task is completed, it moves to the archive with a dated folder.

```bash
tasks                              # Show active tasks
tasks new "title" !priority        # Create (!urgent, !high, !normal, !low)
tasks start <task-id>              # Assign to self
tasks update <task-id> "note"      # Add progress
tasks done <task-id> "summary"     # Complete and archive
```

Why markdown files? Because they're transparent — you can read them in your editor, grep across them, version them with git. The agent sees the same files you do.

### Tasks and messages

Tasks and messages work together. When you send a `!task` message to an agent, it creates both a message (which the agent sees on its next turn) and a backing task file (which tracks progress). When the agent finishes, it sends `!done` back, and the task is archived. This means task delegation has a full paper trail — who asked for what, when it was picked up, what updates were logged, and how it was resolved.

```bash
messages                           # Show unread
messages send @agent !task "do X"  # Delegate work (creates task + message)
messages reply <msg-id> "text"     # Reply to a message
messages done <msg-id> "note"      # Mark a task-message as complete
```

## Projects

Projects group agents, tasks, and goals. Each project has its own agent team, task backlog, and optionally a Telegram topic for discussion.

```bash
metasphere project new <name> [--path P] [--goal "..."] [--member @agent:role]
metasphere project list              # List all projects
metasphere project show [name]       # Project details
metasphere project member add <name> @agent [--role R] [--persistent]
metasphere project wake [name]       # Wake all persistent members
metasphere project chat <name> "msg" # Send to project Telegram topic
```

## Agent Management

```bash
metasphere agent list              # List all agents
metasphere agent spawn @name \
  --scope /path \
  --task "description" \
  --sandbox scoped                 # Spawn child agent
```

Agents can be **ephemeral** (one-shot, run a task and exit) or **persistent** (long-running, with their own tmux session and respawn loop). Persistent agents have a `MISSION.md` that defines their ongoing purpose.

### Sandbox levels

| Level | What the agent can do |
|-------|----------------------|
| `none` | Full access (default) |
| `scoped` | Only files in its assigned directory |
| `nobash` | Read/write/edit but no shell commands |
| `readonly` | Only read and search — can't change anything |

## Sessions

Every persistent agent runs in its own tmux session. The gateway watchdog monitors all sessions and handles stuck prompts, safety confirmations, and restart recovery.

```bash
metasphere session list              # List active sessions
metasphere session restart @agent    # Restart with auto-continuation
metasphere session send @agent "msg" # Inject a message
metasphere session attach @agent     # Attach your terminal
metasphere session stop @agent       # Stop the agent
```

When a session restarts, the watchdog automatically injects a continuation prompt into the fresh instance so it picks up where it left off — no manual intervention needed.

## Scheduling

```bash
metasphere schedule add \
  --name daily-summary \
  --cron "0 9 * * *" \
  --command "python3 -m metasphere.cli.main consolidate run"

metasphere schedule list           # Show scheduled jobs
metasphere schedule remove <name>  # Remove a schedule
```

## Memory

Agents build up persistent memory across sessions:
- **Daily logs** — narrative entries about what happened, what was learned, what surprised
- **LEARNINGS.md** — durable insights that should influence future behavior
- **Searchable index** — full-text search across all past sessions and memory files

```bash
metasphere memory search "query"   # Search agent memory
```

From Telegram: `/memory <query>` searches the same index.

## System Management

```bash
metasphere status                      # System overview
metasphere daemon start|stop|restart   # Daemon control
metasphere logs [gateway|events] [-f]  # View logs
metasphere update                      # Update from git (pull + reinstall + restart)
metasphere config                      # Show configuration
```

## Telegram Bot Commands

All slash commands available in your Telegram chat:

```
/status              System overview
/tasks               Active tasks (card format)
/messages            Inbox messages
/agents              All agents across projects
/projects            Project list (card format)
/projects show <n>   Project details
/schedule            Scheduled jobs
/schedule run <n>    Trigger a job
/team                Team management
/send @agent !label msg
/memory <query>      Search agent memory
/events              Recent events
/session             Restart agent REPL
/help                Show help
```

You can also send plain text — it goes directly into the agent's session as a new message, and the agent will see it on its very next turn.

## Telegram Setup

1. Create a bot via [@BotFather](https://t.me/BotFather) on Telegram
2. Copy the bot token
3. Run `metasphere config telegram <token>`
4. Start the daemon: `metasphere daemon start`
5. Message your bot

The gateway polls for new messages, injects them into the agent's session, and forwards responses back.

## Agent Identity

Each agent has persona files that define its voice, knowledge, and objectives:

```
~/.metasphere/agents/@orchestrator/
├── SOUL.md              # Identity, values, voice
├── USER.md              # Who the human is
├── MISSION.md           # Objectives and responsibilities
├── HEARTBEAT.md         # Current status (overwritten each update)
├── LEARNINGS.md         # Accumulated insights
└── daily/YYYY-MM-DD.md  # Daily narrative logs
```

The orchestrator reads `SOUL.md` and `USER.md` at session start to establish its voice. Everything else is loaded on demand.

## Migration from OpenClaw

```bash
metasphere migrate detect          # Check what will be migrated
metasphere migrate run             # Migrate everything
```

Migrates: Telegram bot token, SOUL.md, memory files, and session history.

## License

[Functional Source License, Version 1.1, Apache 2.0 Future License](LICENSE)
(`FSL-1.1-Apache-2.0`).

You can read, fork, modify, and use the code for anything *except* offering
it as a commercial product or service that competes with what we offer using
the same software. Two years after each release, that release auto-converts
to the standard Apache License 2.0 — fully open source.

See [`LICENSE`](LICENSE) for the full text and <https://fsl.software/> for
background on the license shape.

## Contributing

See `CHANGELOG.md` for recent changes, `docs/CLI.md` for the full CLI reference, and `CLAUDE.md` for the operational instructions that guide the agent.
