# scripts/ — Metasphere CLI tools

These are the executable surface of the metasphere harness. The installer (`../install.sh`) copies (or symlinks) them into `~/.metasphere/bin/` and they become the user-facing commands `messages`, `tasks`, `metasphere`, etc.

Two scripts are *always* live as long-running daemons:

| Script | Run as | Purpose |
|---|---|---|
| `metasphere-gateway` | systemd user service / launchd job | Polls Telegram, injects user messages into the persistent `metasphere-orchestrator` tmux session, runs the supervisor/watchdog (revives the Claude REPL on config change or stuck-prompt). This is the heart of the harness. |
| `metasphere-telegram-stream` | (subset of gateway, can run standalone) | Polls Telegram and archives messages to `~/.metasphere/telegram/stream/YYYY-MM-DD.jsonl`, indexes to CAM. The gateway calls its `once` mode internally. |

The rest are user-facing CLIs and helpers:

| Script | Purpose |
|---|---|
| `metasphere` | Top-level user CLI: `metasphere status`, `ls`, `agents`, `events`, `project`, `schedule`, `agent spawn`, etc. |
| `metasphere-context` | `UserPromptSubmit` hook. Runs before each Claude turn, injects current scope's identity, mission, heartbeat, learnings, recent events, pending messages, and active tasks into the prompt. **This is what makes an agent feel "self-aware".** |
| `metasphere-spawn` | Spawn a child agent with its own identity, scope, task, and parent pointer. |
| `metasphere-agent` | Manage agent state files (`status`, `heartbeat`, `sunset`, `tree`, `subtree`, `report`). |
| `metasphere-events` | Append to and query the event log (`metasphere-events log <type> "msg" --agent @x`). |
| `metasphere-heartbeat` | Pulse an agent's `HEARTBEAT.md`, used by long-running sessions to prove they're alive. |
| `metasphere-identity` | Read/write an agent's `SOUL.md`, `MISSION.md`, `LEARNINGS.md`, etc. |
| `metasphere-migrate` | OpenClaw → metasphere migration: extracts `~/.openclaw/openclaw.json` config (telegram bot token at `channels.telegram.botToken`, etc.), agent identities, soul, scheduled tasks. |
| `metasphere-project` | Per-project init / status / changelog / learnings aggregation. |
| `metasphere-schedule` | Cron-style scheduler for recurring agents. Backed by user systemd timers (linux) or launchd (mac). |
| `metasphere-session` | Session lifecycle helpers (start/stop/state). |
| `metasphere-tmux-submit` | Helper sourced by the gateway. Reliably types text into a tmux pane using literal mode + a paste-placeholder watchdog (works around the historic flakiness of `tmux send-keys` with long messages and IME). |
| `metasphere-trace` | Capture command output as a trace file under `~/.metasphere/traces/`. |
| `metasphere-fts` | Full-text search across messages/tasks/events. |
| `metasphere-telegram` | Interactive Telegram operations: `notify`, `webhook`, slash-command processing (called by the gateway when a message starts with `/`). |
| `metasphere-telegram-groups` | Group/topic routing for Telegram supergroups. |
| `metasphere-git-hooks` | Install repo git hooks (auto-commit on session-complete, etc.). |
| `metasphere-posthook` | Post-tool-use hook for Claude Code. |
| `messages` | Fractal messaging CLI. See [`../.messages/README.md`](../.messages/README.md). |
| `tasks` | Fractal tasks CLI. See [`../.tasks/README.md`](../.tasks/README.md). |

## Mental model

```
   Telegram user
        │
        ▼
  metasphere-gateway (daemon)
        │  poll getUpdates
        │  → process_user_message
        │  → submit_to_tmux  ── via metasphere-tmux-submit
        ▼
  tmux session "metasphere-orchestrator"
        │
        ▼
  Claude Code (Opus) running with .claude/settings.json
        │
        │  on every UserPromptSubmit:
        ▼
  metasphere-context  ── injects identity + messages + tasks
        │
        ▼
  Claude responds → captured → sent back to Telegram via gateway
```

Two installations of this exact stack run in parallel — one on **wintermute** (mac, your laptop) and one on **spot** (linux, inside the openclaw nspawn container on data.basicbold.de). They don't share messages or tasks; they share **memory** via CAM (Collective Agent Memory), which syncs across machines. Each installation has its own Telegram bot token and thus its own personality/conversation thread.
