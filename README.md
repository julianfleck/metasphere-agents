# Metasphere Agents

A multi-agent orchestration harness for Claude Code with Telegram as the human interface.

> This repo is both the harness AND its first test subject. It evolves itself.

## Features

- **Telegram Gateway**: Persistent Claude Code session with Telegram polling for human-in-the-loop interaction
- **Fractal Scoping**: Hierarchical task and message management with upward visibility
- **Agent Spawning**: Create specialized child agents with scoped permissions
- **CAM Integration**: Collective Agent Memory for searchable session history
- **OpenClaw Migration**: Backwards compatibility migration from OpenClaw installations
- **Timezone Support**: UTC storage with user-preferred timezone display

## Installation

```bash
# Clone the repository
git clone https://github.com/julianfleck/metasphere-agents.git
cd metasphere-agents

# Run the installer
./install.sh
```

The installer will:
1. Create `~/.metasphere/` runtime directory
2. Install scripts to `~/.metasphere/bin/`
3. Set up the @orchestrator agent identity
4. Configure launchd (macOS) or systemd (Linux) daemon
5. Verify Claude Code authentication
6. Optionally migrate from OpenClaw

### Requirements

- **Claude Code CLI** - Must be authenticated (`claude /login`)
- **tmux** - For persistent sessions (`brew install tmux`)
- **jq** - For JSON processing (`brew install jq`)
- **curl** - For Telegram API calls

## Configuration

Configure Telegram and timezone in `~/.metasphere/config`:

```bash
# Set Telegram bot token
metasphere config telegram <your-bot-token>

# Set your timezone for agent context
metasphere config timezone Europe/Berlin

# View current configuration
metasphere config
```

## Quick Start

```bash
# Check system status
metasphere status

# Start the gateway daemon
metasphere daemon start

# View logs
metasphere logs          # Gateway logs
metasphere logs events   # Event stream
metasphere logs -f       # Follow mode

# Send a test message via Telegram or CLI
metasphere telegram send "Hello from Metasphere!"
```

## Commands

### Core Commands

```bash
metasphere status              # System overview
metasphere ls                  # Project landscape
metasphere daemon start|stop|restart|status
metasphere logs [gateway|events|error] [-f]
metasphere update              # Update from git source
```

### Configuration

```bash
metasphere config                        # Show all config
metasphere config telegram <token>       # Set Telegram token
metasphere config timezone <tz>          # Set timezone (e.g., America/New_York)
```

### Agent Management

```bash
metasphere agent list                    # List agents
metasphere agent spawn @name --scope /path --task "description"
metasphere agent update @name --status "working: task"
metasphere agent report @name "progress note"
```

### Tasks

Tasks use hierarchical naming: `project/@agent/taskname`

```bash
tasks                              # Show active tasks
tasks new "title" !priority        # Create task (!urgent, !high, !normal, !low)
tasks new "title" --name heartbeat # Explicit naming
tasks start <task-id>              # Start working on task
tasks update <task-id> "note"      # Add progress update
tasks done <task-id> "summary"     # Complete task
tasks tree                         # Show task tree across scopes
```

### Messages

```bash
messages                           # Show unread
messages all                       # Show all
messages send @target !label "msg" # Send message
messages reply <msg-id> "text"     # Reply
messages done <msg-id> "note"      # Mark complete
```

### Interactive Sessions

```bash
metasphere session start @agent    # Start tmux session
metasphere session attach @agent   # Attach to session
metasphere session send @agent "msg" # Send input
metasphere session list            # List sessions
metasphere session stop @agent     # Stop session
```

### Migration

```bash
metasphere migrate detect          # Detect OpenClaw installation
metasphere migrate run             # Migrate OpenClaw data
metasphere migrate run --disable   # Migrate and disable OpenClaw
metasphere migrate telegram        # Migrate Telegram token only
metasphere migrate sessions        # Trigger CAM session indexing
```

## Architecture

### Directory Structure

```
~/.metasphere/                    # Runtime directory
├── config                        # Configuration file
├── agents/@orchestrator/         # Agent identity
│   ├── SOUL.md                   # Identity and values
│   ├── HEARTBEAT.md              # Current status
│   ├── LEARNINGS.md              # Accumulated insights
│   └── MISSION.md                # Objectives
├── bin/                          # Installed scripts
├── logs/                         # Log files
│   ├── gateway.log               # Gateway daemon log
│   ├── error.log                 # Error log
│   └── events/                   # Event stream
└── telegram/
    └── stream/                   # Telegram message archive
```

### Fractal Scoping

Every directory can have `.tasks/` and `.messages/` subdirectories:

```
project/
├── .tasks/active/                # Project-level tasks
├── .messages/inbox/              # Project-level messages
├── src/
│   ├── .tasks/active/            # src-level tasks
│   └── components/
│       └── .tasks/active/        # Component-level tasks
```

Agents see content from their scope + all parent scopes (upward visibility).

### Sandbox Levels

When spawning agents, control their permissions:

| Level | Description |
|-------|-------------|
| `none` | Full access (default) |
| `scoped` | Restricted to agent's scope directory |
| `nobash` | No Bash tool (safer for untrusted tasks) |
| `readonly` | Only Read, Glob, Grep tools |

```bash
metasphere agent spawn @untrusted --scope /path --sandbox=readonly
```

## OpenClaw Migration

For users migrating from OpenClaw:

```bash
# Check what will be migrated
metasphere migrate detect

# Migrate everything and disable OpenClaw
metasphere migrate run --disable
```

This migrates:
- Telegram bot token from `~/.openclaw/openclaw.json`
- SOUL.md and memory files
- Triggers CAM to index existing sessions

## Updating

```bash
# Update from git source
metasphere update

# This will:
# 1. Git pull latest changes
# 2. Copy updated scripts
# 3. Restart the daemon
```

## Daemon Management

### macOS (launchd)

```bash
# Start/stop
metasphere daemon start
metasphere daemon stop

# View status
metasphere daemon status

# Manual control
launchctl load ~/Library/LaunchAgents/com.metasphere.plist
launchctl unload ~/Library/LaunchAgents/com.metasphere.plist
```

### Linux (systemd)

```bash
systemctl --user start metasphere
systemctl --user stop metasphere
systemctl --user status metasphere
```

## Development

The repository structure:

```
metasphere-agents/
├── scripts/                      # CLI tools
│   ├── metasphere                # Main CLI
│   ├── metasphere-gateway        # Telegram gateway daemon
│   ├── metasphere-agent          # Agent management
│   ├── metasphere-session        # Interactive sessions
│   ├── metasphere-migrate        # OpenClaw migration
│   ├── tasks                     # Task management
│   └── messages                  # Message management
├── templates/                    # Agent templates
├── docs/                         # Documentation
│   └── ARCHITECTURE.md           # Architecture details
├── install.sh                    # Installer
└── claude.md                     # Operational instructions
```

## License

[Functional Source License, Version 1.1, Apache 2.0 Future License](LICENSE)
(`FSL-1.1-Apache-2.0`).

You can read, fork, modify, and use the code for anything *except* offering
it as a commercial product or service that competes with what we offer using
the same software. Two years after each release we publish, that release
auto-converts to the standard Apache License 2.0 — fully open source.

See [`LICENSE`](LICENSE) for the full text and <https://fsl.software/> for
background on the license shape.

## Contributing

This project evolves itself through the Evolution Loop:

1. **Identify** - What needs improvement?
2. **Experiment** - Make a targeted change
3. **Evaluate** - Did it improve things?
4. **Integrate** - Keep or discard
5. **Loop** - Continue to next improvement

See `CHANGELOG.md` for recent changes.
