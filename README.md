# Metasphere Agents

A multi-agent orchestration harness for Claude Code. Persistent sessions, Telegram as the human interface, fractal task/message scoping, agent spawning, scheduled automation.

> Coming from OpenClaw? Metasphere is the successor. Run `metasphere migrate run` to bring your identity, memory, and Telegram bot across. Everything carries over.

## What it does

- **Persistent Claude Code sessions** — runs in tmux, survives disconnects, picks up where it left off
- **Telegram as your interface** — talk to your agent from your phone. It reads your messages, thinks, responds, forwards tool output
- **Agent spawning** — break complex work into child agents with scoped permissions. They report back when done
- **Fractal tasks and messages** — every directory can have `.tasks/` and `.messages/`. Agents see their scope + all parent scopes
- **Scheduled automation** — cron-style scheduling for recurring tasks (market scans, memory consolidation, health checks)
- **Claude Code hooks** — context injection before each turn (messages, tasks, memory, persona). Stop hook routes output to Telegram
- **OpenClaw migration** — one command to migrate your identity, memory, Telegram token, and session history

## Installation

```bash
git clone https://github.com/julianfleck/metasphere-agents.git
cd metasphere-agents
./install.sh
```

### Prerequisites

- **Claude Code CLI** — authenticated (`claude /login`)
- **tmux** — persistent sessions (`brew install tmux` / `apt install tmux`)
- **jq** — JSON processing (`brew install jq` / `apt install jq`)
- **curl** — HTTP requests (usually pre-installed)
- **Python 3.11+** — for the Python package

### What the installer does

1. Creates `~/.metasphere/` runtime directory
2. Installs scripts to `~/.metasphere/bin/` and adds to PATH
3. Sets up the @orchestrator agent identity (SOUL.md, MISSION.md, etc.)
4. Configures launchd (macOS) or systemd (Linux) daemon
5. Verifies Claude Code authentication
6. Optionally migrates from OpenClaw

## Quick Start

```bash
# Configure Telegram bot token (get one from @BotFather)
metasphere config telegram <your-bot-token>

# Set your timezone
metasphere config timezone Europe/Berlin

# Start the daemon
metasphere daemon start

# Check status
metasphere status

# Send a test message
metasphere telegram send "Hello from Metasphere!"
```

Your agent is now running. Message it on Telegram.

## Commands

### System

```bash
metasphere status                      # System overview
metasphere daemon start|stop|restart   # Daemon control
metasphere logs [gateway|events] [-f]  # View logs
metasphere update                      # Update from git
metasphere config                      # Show configuration
metasphere config telegram <token>     # Set Telegram token
metasphere config timezone <tz>        # Set timezone
```

### Tasks

Tasks are file-based and hierarchical. They live in `.tasks/active/` directories and survive across sessions.

```bash
tasks                              # Show active tasks in scope
tasks new "title" !priority        # Create (!urgent, !high, !normal, !low)
tasks start <task-id>              # Assign to self
tasks update <task-id> "note"      # Add progress
tasks done <task-id> "summary"     # Complete
tasks tree                         # Show task tree across scopes
```

### Messages

File-based inter-agent messaging with labels.

```bash
messages                           # Show unread in scope
messages all                       # Show all including read
messages send @target !label "msg" # Send (!task, !urgent, !info, !query, !done)
messages reply <msg-id> "text"     # Reply
messages done <msg-id> "note"      # Mark complete
```

### Agent Management

```bash
metasphere agent list              # List all agents
metasphere agent spawn @name \
  --scope /path \
  --task "description" \
  --sandbox scoped                 # Spawn child agent

metasphere agent update @name --status "working: task"
metasphere agent report @name "progress note"
```

### Sessions

```bash
metasphere session list              # List active sessions
metasphere session info @agent       # Session details
metasphere session attach @agent     # Attach to session
metasphere session send @agent "msg" # Send input
metasphere session restart @agent    # Restart claude (respawn loop + auto-continuation)
metasphere session stop @agent       # Stop session
```

When a session restarts (via `restart` or the agent issuing `/exit`), the gateway watchdog automatically injects a continuation prompt into the fresh Claude instance after an 8-second grace period. The context hook fires on that prompt, giving the new instance its full persona, messages, and tasks — no manual intervention needed.

### Scheduling

```bash
metasphere schedule add \
  --name daily-summary \
  --cron "0 9 * * *" \
  --command "python3 -m metasphere.cli.main consolidate run"

metasphere schedule list           # Show scheduled tasks
metasphere schedule remove <name>  # Remove a schedule
```

### Migration from OpenClaw

```bash
metasphere migrate detect          # Check what will be migrated
metasphere migrate run             # Migrate everything
metasphere migrate run --disable   # Migrate and disable OpenClaw
```

Migrates: Telegram bot token, SOUL.md, memory files, session history (via CAM indexing).

### Projects

```bash
metasphere project new <name> [--path P] [--goal "..."] [--member @agent:role]
metasphere project list              # List all projects
metasphere project show [name]       # Project details
metasphere project member add <name> @agent [--role R] [--persistent]
metasphere project wake [name]       # Wake all persistent members
metasphere project chat <name> "msg" # Send to project Telegram topic
```

### Telegram Bot Commands

These slash commands are available in the Telegram chat with your bot:

```
/status              System overview
/tasks               List active tasks (card format)
/messages            Show inbox messages
/agents              List all agents across projects
/projects            List projects (card format)
/projects show <n>   Project details
/schedule            Show scheduled jobs
/schedule run <n>    Trigger a job manually
/team                Project teams: /team [status|specs|seed|wake]
/specs               List available agent specs
/send @agent !label message
/cam <query>         Search agent memory
/events              Tail recent events
/session             Restart orchestrator REPL
/spot                Remote host status
/help                Show help
/ping                Ping the bot
```

You can also send free-text messages — they're injected directly into the orchestrator's Claude Code session.

## Claude Code Hooks

Metasphere uses Claude Code's hook system to inject context and route output:

### UserPromptSubmit (pre-turn)

The `metasphere-context` hook runs before each agent turn, injecting:
- Unread messages from current scope + parent scopes
- Active tasks from current scope + parent scopes
- Voice capsule (persona excerpt from SOUL.md)
- Recent events (heartbeats, schedule fires, agent completions)
- Memory context (FTS search against CAM)

### Stop (post-turn)

The `metasphere-posthook` routes assistant output:
- Forwards text to Telegram (if configured)
- Logs events to the event stream
- Updates heartbeat state

### Configuration

Hooks are configured in `.claude/settings.json` (repo-level) or `.claude/settings.local.json` (user-level):

```json
{
  "hooks": {
    "UserPromptSubmit": [{
      "command": "metasphere-context"
    }],
    "Stop": [{
      "command": "python3 -m metasphere.cli.posthook"
    }]
  }
}
```

## Architecture

### Runtime Directory

```
~/.metasphere/
├── config                        # Telegram token, timezone, settings
├── agents/@orchestrator/         # Agent identity files
│   ├── SOUL.md                   # Identity, values, voice
│   ├── USER.md                   # Who the human is
│   ├── HEARTBEAT.md              # Current status
│   ├── LEARNINGS.md              # Accumulated insights
│   ├── MISSION.md                # Objectives
│   └── daily/YYYY-MM-DD.md      # Daily narrative logs
├── bin/                          # Installed scripts
├── logs/                         # Gateway, error, event logs
├── schedules/                    # Cron definitions
└── telegram/stream/              # Telegram message archive
```

### Fractal Scoping

Every directory can have `.tasks/` and `.messages/` subdirectories. Agents see content from their scope + all parent scopes (upward visibility):

```
project/
├── .tasks/active/                # Project-level tasks
├── .messages/inbox/              # Project-level messages
├── src/
│   ├── .tasks/active/            # src-level tasks (sees project tasks too)
│   └── components/
│       └── .tasks/active/        # Component tasks (sees src + project)
```

### Sandbox Levels

Control what spawned agents can do:

| Level | Description |
|-------|-------------|
| `none` | Full access (default) |
| `scoped` | Restricted to agent's scope directory |
| `nobash` | No Bash tool |
| `readonly` | Only Read, Glob, Grep tools |

### Agent Identity

Each agent has persona files that define its voice, knowledge, and objectives. The orchestrator reads `SOUL.md` and `USER.md` at session start, lazy-loads everything else via `persona-index.md`.

The SPIRAL cognitive loop guides each turn: **Sample** (check context) → **Pursue** (explore) → **Integrate** (connect) → **Reflect** (evaluate) → **Abstract** (synthesize) → **Loop** (continue).

## Telegram Setup

1. Create a bot via [@BotFather](https://t.me/BotFather) on Telegram
2. Copy the bot token
3. Run `metasphere config telegram <token>`
4. Start the daemon: `metasphere daemon start`
5. Message your bot — it forwards to Claude Code, responses come back

The gateway polls Telegram for new messages, injects them into the Claude Code session via the heartbeat system, and forwards assistant responses back.

## Daemon Management

### macOS (launchd)

```bash
metasphere daemon start            # launchctl load
metasphere daemon stop             # launchctl unload
metasphere daemon status           # Check if running
```

### Linux (systemd)

```bash
metasphere daemon start            # systemctl --user start
metasphere daemon stop             # systemctl --user stop
systemctl --user status metasphere # Detailed status
journalctl --user -u metasphere -f # Follow logs
```

## Updating

```bash
metasphere update
```

Pulls latest from git, reinstalls scripts, restarts the daemon.

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

Metasphere evolves itself through a continuous improvement loop:

1. **Identify** — friction in a workflow, missing functionality, confusion
2. **Experiment** — make a targeted change, keep it small
3. **Evaluate** — test in real operation
4. **Integrate** — keep or revert, update LEARNINGS.md either way

See `CHANGELOG.md` for recent changes, `docs/CLI.md` for the full CLI reference, and `CLAUDE.md` for the operational instructions that guide the agent.
