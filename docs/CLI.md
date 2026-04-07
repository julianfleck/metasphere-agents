# Metasphere CLI Reference

The metasphere CLI surface as of 2026-04-07. This document covers every executable script in `scripts/` and the verbs routed through the unified `metasphere` dispatcher. All scripts are bash. State lives under `$METASPHERE_DIR` (default `~/.metasphere`) and fractal `.messages/` + `.tasks/` directories at every scope level in the repo.

## Contents

- [metasphere (dispatcher)](#metasphere-dispatcher)
- [messages](#messages)
- [tasks](#tasks)
- [metasphere-identity](#metasphere-identity)
- [metasphere-context](#metasphere-context)
- [metasphere-posthook](#metasphere-posthook)
- [metasphere-agent](#metasphere-agent)
- [metasphere-spawn](#metasphere-spawn)
- [metasphere-wake](#metasphere-wake)
- [metasphere-session](#metasphere-session)
- [metasphere-gateway](#metasphere-gateway)
- [metasphere-tmux-submit](#metasphere-tmux-submit)
- [metasphere-heartbeat](#metasphere-heartbeat)
- [metasphere-schedule](#metasphere-schedule)
- [metasphere-events](#metasphere-events)
- [metasphere-trace](#metasphere-trace)
- [metasphere-fts](#metasphere-fts)
- [metasphere-project](#metasphere-project)
- [metasphere-git-hooks](#metasphere-git-hooks)
- [metasphere-migrate](#metasphere-migrate)
- [metasphere-telegram](#metasphere-telegram)
- [metasphere-telegram-stream](#metasphere-telegram-stream)
- [metasphere-telegram-groups](#metasphere-telegram-groups)

---

## metasphere (dispatcher)

Top-level user-facing CLI. Routes verbs to the specialized scripts, implements a few commands directly (status/ls/agents/daemon/update/logs/config/telegram setup).

### Directly implemented subcommands

```
metasphere status              # Comprehensive system status (gateway, telegram, CAM, agents, tasks, messages, last telegram)
metasphere st                  # alias
metasphere ls [path|@agent]    # Project landscape; @agent form shows agent detail
metasphere list                # alias for ls
metasphere agents              # List registered agents under ~/.metasphere/agents/
metasphere ag                  # alias
metasphere gateway status      # Gateway/Telegram/queue/logs summary
metasphere gw                  # alias
metasphere run [interval]      # exec metasphere-gateway daemon <interval> in foreground
metasphere daemon start        # Start launchd/systemd unit
metasphere daemon stop
metasphere daemon restart
metasphere daemon status       # delegates to cmd_gateway_status
metasphere daemon restart-orchestrator    # delegates to metasphere-gateway restart-orchestrator
metasphere daemon restart-claude          # alias
metasphere daemon logs         # tail -f metasphere.log / gateway.log
metasphere d ...               # alias
metasphere telegram setup      # Interactive bot token wizard
metasphere tg setup            # alias
metasphere config              # show config
metasphere config timezone [TZ]     # get/set ~/.metasphere/config/timezone
metasphere config tz [TZ]           # alias
metasphere update              # git pull + reinstall + restart gateway
metasphere upgrade             # alias
metasphere logs [-f] [all|gateway|error|events]   # tail logs
metasphere log ...             # alias
metasphere version | -v | --version
metasphere help | -h | --help | (empty)
```

### Verbs delegated to other scripts

| Verb (+ aliases) | Delegates to | Notes |
|---|---|---|
| `spawn` | `metasphere-spawn` | ephemeral one-shot agent |
| `wake` | `metasphere-wake` | persistent collaborator |
| `agent` / `ag` | `metasphere-agent` | plus `agent wake` → `metasphere-wake`, `agent spawn` → `metasphere-spawn` |
| `send` / `msg` | `messages send` | quick send shortcut |
| `tasks` / `task` / `t` | `tasks` | |
| `schedule` / `sched` / `cron` | `metasphere-schedule` | |
| `telegram` / `tg` | `metasphere-telegram-stream` (default); `telegram groups` → `metasphere-telegram-groups`; `telegram setup` handled internally |
| `context` | `metasphere-context` | |
| `trace` / `tr` | `metasphere-trace` | |
| `session` / `sess` / `s` | `metasphere-session` | |
| `events` / `ev` / `e` | `metasphere-events` | |
| `git-hooks` / `hooks` | `metasphere-git-hooks` | |
| `project` / `proj` / `p` | `metasphere-project` | |
| `migrate` / `migration` | `metasphere-migrate` | |

### Files read/written

- `~/.metasphere/config/telegram.env` (read, sourced)
- `~/.metasphere/config/telegram_chat_id` (read)
- `~/.metasphere/config/timezone` (read/write)
- `~/.metasphere/agents/@*/` (read: status, scope, task, parent)
- `~/.metasphere/projects.json` (read)
- `~/.metasphere/telegram/latest.json` (read)
- `~/.metasphere/telegram/offset` (read)
- `~/.metasphere/telegram/processed` (read)
- `~/.metasphere/events/events.jsonl` (read)
- `~/.metasphere/logs/gateway.log`, `metasphere.log`, `metasphere.error.log` (read)
- `$REPO_ROOT/.tasks/active/*.md`, `.messages/inbox/*.msg` (read via find)

### Env vars consumed

- `METASPHERE_REPO_ROOT` (optional; repo root override)
- `METASPHERE_DIR` (optional; runtime dir override)
- `HOME`

### Dependencies

`metasphere-gateway`, `metasphere-spawn`, `metasphere-wake`, `metasphere-agent`, `messages`, `tasks`, `metasphere-schedule`, `metasphere-telegram-stream`, `metasphere-telegram-groups`, `metasphere-context`, `metasphere-trace`, `metasphere-session`, `metasphere-events`, `metasphere-git-hooks`, `metasphere-project`, `metasphere-migrate`. External: `git`, `jq`, `tmux`, `curl`, `sqlite3`, `cam`, `launchctl`/`systemctl`.

---

## messages

Fractal inter-agent messaging. Messages are markdown files with YAML frontmatter under `.messages/inbox/` and `.messages/outbox/` at each scope. Agents see messages in their scope plus parent scopes (upward visibility).

### Subcommands

```
messages                              # inbox: show unread across scope+parents
messages all                          # inbox: show all, including read
messages send @target !label "msg"    # send; also wakes recipient tmux session if live (unless sender is @user)
messages reply <msg-id> "response"    # mark original replied, send reply to original sender
messages done <msg-id> ["note"]       # mark original completed, optionally send !done to sender
messages read <msg-id>                # mark as read, cat the message
messages tree                         # walk .messages/ dirs and count inbox/outbox per scope
messages status [msg-id]              # show agent statuses, or a single message's status lifecycle
```

Targets:

- `@.` — current scope
- `@..` — parent scope
- `@/path/` — absolute path from repo root
- `@name` — named agent (resolves via `~/.metasphere/agents/@name/scope`, falls back to repo root)

Labels used by callers: `!task`, `!urgent`, `!info`, `!query`, `!done`, `!reply`, `!report`, `!error`, `!sunset`.

### Message file format

```
---
id: msg-<epoch>-<pid>
from: @agent
to: @target
label: !label
status: unread|read|replied|completed
scope: /relative/path
created: ISO8601
read_at:
replied_at:
completed_at:
reply_to: <orig-msg-id-or-empty>
---

<body>
```

### Files read/written

- `<scope>/.messages/inbox/*.msg` — written on send, read on inbox/reply/done
- `<scope>/.messages/outbox/*.msg` — written as copy on send/reply
- `~/.metasphere/agents/@<name>/scope` — read to resolve named targets
- `~/.metasphere/agents/*/status` — read for `messages status`

### Env vars consumed

- `METASPHERE_REPO_ROOT`, `METASPHERE_DIR`, `METASPHERE_SCOPE`, `METASPHERE_AGENT_ID`

### Dependencies

Sources `metasphere-identity`. Calls `metasphere-events log`, `metasphere-agent activity --sent`, and sources `metasphere-tmux-submit` for the wake path.

---

## tasks

Fractal task management. Tasks are markdown files with YAML frontmatter in `.tasks/active/` and `.tasks/completed/`. Hierarchical IDs (project/agent/name) are supported.

### Subcommands

```
tasks                                 # active tasks in scope+parents (default)
tasks list [all|completed]            # filter
tasks new "title" [!priority] [--name id]    # create; priorities: !urgent !high !normal !low
tasks start <id>                      # set status=in-progress, assigned_to=<agent>
tasks update <id> "note"              # append an update line
tasks done <id> ["summary"]           # mark completed, move to completed/
tasks show <id>                       # cat the file
tasks tree                            # walk .tasks/ dirs and count
```

### Task file format

```
---
id: hierarchical/id
title: ...
priority: !normal
status: pending|in-progress|blocked|completed
scope: /rel/path
created: ISO8601
created_by: @agent
assigned_to:
started_at:
completed_at:
---

# title
## Description / Acceptance Criteria / Updates / Subtasks / Notes
```

### Files read/written

- `<scope>/.tasks/active/**.md`, `<scope>/.tasks/completed/**.md`

### Env vars consumed

- `METASPHERE_REPO_ROOT`, `METASPHERE_DIR`, `METASPHERE_SCOPE`, `METASPHERE_AGENT_ID` (via identity)

### Dependencies

Sources `metasphere-identity`. Calls `metasphere-events log`.

---

## metasphere-identity

Tiny helper sourced by `messages`, `tasks`, and `metasphere-events` to resolve the current agent ID.

### Resolution order

1. `$METASPHERE_AGENT_ID`
2. `~/.metasphere/current_agent` pointer file
3. `@orchestrator` (if `~/.metasphere/agents/@orchestrator/` exists)
4. `@user`

### Usage

```
source metasphere-identity
AGENT="$(resolve_agent_id)"

# or standalone
metasphere-identity            # prints resolved agent
```

---

## metasphere-context

The per-turn hook invoked on `UserPromptSubmit` (configured in `.claude/settings.json`). Emits a delta block on stdout that Claude Code injects into context.

Order of emission:

1. Lightweight per-turn header (`# Metasphere Delta (@agent)` + status line)
2. Harness drift warning — sha256 of CLAUDE.md, settings.json, settings.local.json, metasphere-context compared against baseline at `~/.metasphere/state/harness_hash_baseline`
3. Telegram context (capped at 1 KB, via `metasphere-telegram-stream context --history 3`)
4. Child agent reports under `~/.metasphere/agents/@<agent>/child_reports/`
5. `messages` output
6. `tasks` output
7. `metasphere-events tail 10`
8. Memory Context via `metasphere-fts "<task-keywords> <project-name>" 5`

### Usage

Invoked by the harness. Also exposed via `metasphere context`.

### Files read

- `~/.metasphere/state/harness_hash_baseline`
- `~/.metasphere/agents/@<agent>/{status,task,child_reports/*.md}`
- `$REPO_ROOT/{CLAUDE.md,.claude/settings.json,.claude/settings.local.json,scripts/metasphere-context}`

### Env vars

- `METASPHERE_REPO_ROOT`, `METASPHERE_DIR`, `METASPHERE_AGENT_ID`, `METASPHERE_SCOPE` (exported for children)

### Dependencies

`messages`, `tasks`, `metasphere-events`, `metasphere-telegram-stream`, `metasphere-fts`.

---

## metasphere-posthook

Stop-hook handler. Reads Claude Code's Stop event JSON from stdin, extracts the last assistant text from `transcript_path`, and (for the orchestrator only) routes it to Telegram via `metasphere-telegram-stream send`. Also bumps the `--turn` activity counter, upgrades status from `spawned` to `active`, and logs a heartbeat event every 10 turns.

### Usage

Invoked by the harness via Stop hook. Not called directly by users.

### Files read/written

- stdin: Stop hook JSON (`session_id`, `transcript_path`, `stop_hook_active`)
- `<transcript_path>` (read)
- `~/.metasphere/state/posthook_last_sent` (dedupe hash)
- `~/.metasphere/state/posthook_telegram_errors.log` (failure log)
- `~/.metasphere/agents/@<agent>/{status,updated_at,activity.json}`

### Env vars

- `METASPHERE_AGENT_ID`, `METASPHERE_DIR`

### Dependencies

`jq`, `sha256sum`/`shasum`, `metasphere-telegram-stream`, `metasphere-agent activity`, `metasphere-events`.

---

## metasphere-agent

Agent lifecycle management. Handles spawn, update, sunset, resume, status (with inferred state), tree, subtree, cleanup, activity tracking, report, view.

### Subcommands

```
metasphere-agent spawn|new @name --scope PATH --task "..." [--parent @p] [--soul FILE] [--sandbox LEVEL] [--interactive|-i]
metasphere-agent update|set @name --status "working: X" [--note "..."]
metasphere-agent sunset|retire|stop @name [--reason "..."] [--skip-docs]
metasphere-agent resume|start @name ["new task"]
metasphere-agent status|show [@name]       # with inferred state (tmux alive, last activity, msg counts)
metasphere-agent list|ls                   # same as status (no arg)
metasphere-agent tree|hierarchy             # parent→children tree, orphans, unlinked
metasphere-agent subtree @name              # recurse children of a single agent
metasphere-agent cleanup|clean              # detect stale spawns, missing scopes, stuck working, orphaned parents
metasphere-agent activity [--sent|--received|--command|--turn]   # increment activity.json counters
metasphere-agent report @name "message"     # write a report file, bubble up to parent
metasphere-agent view @name                 # show what agent sees (self, child reports, subtree stats, pending messages)
```

Sandbox levels: `none`, `scoped`, `nobash`, `readonly` (child inherits parent's or stricter).

### Agent directory layout

```
~/.metasphere/agents/@<name>/
├── SOUL.md                    # persona (persists)
├── MISSION.md                 # objectives (presence = persistent agent)
├── HEARTBEAT.md               # operational status
├── LEARNINGS.md               # insights
├── status                     # single-line status
├── scope                      # working dir
├── task                       # task description
├── parent                     # parent agent id
├── sandbox                    # sandbox level
├── children                   # one child id per line
├── spawned_at, updated_at     # ISO8601 timestamps
├── activity.json              # {messages_sent, messages_received, commands_run, turns, last_activity}
├── session.log                # log-style transcript
├── session_started            # session start timestamp
├── learnings/YYYY-MM-DD-*.md
├── history/YYYY-MM-DD-*.json
├── reports/YYYY-MM-DD-HHMMSS.md
├── child_reports/<child>-<ts>.md
└── output.log, pid, harness.md   # from metasphere-spawn
```

### Env vars

- `METASPHERE_DIR`, `METASPHERE_AGENT_ID` (for activity tracking default)

### Dependencies

`jq`, `tmux`, `metasphere-events`, `metasphere-session`, `messages`.

---

## metasphere-spawn

Spawn an ephemeral (one-shot) agent at a scope. Writes identity files, generates a harness, and launches `claude -p` detached with the harness as prompt.

### Usage

```
metasphere-spawn @agent-name /scope/path/ "task description" [@parent]
```

`@parent` defaults to `@orchestrator`. Scope is resolved relative to `$REPO_ROOT`.

### Side effects

- Creates `<scope>/.tasks/{active,completed}` and `<scope>/.messages/{inbox,outbox}`
- Writes `~/.metasphere/agents/@<name>/{task,status,scope,parent,spawned_at,harness.md}`
- Sends `!task` message to scope inbox
- Launches `nohup claude -p "$(cat harness.md)" --dangerously-skip-permissions` with stdin from /dev/null, output to `output.log`, pid in `pid`
- Logs spawn event

### Env vars

- `METASPHERE_REPO_ROOT`, `METASPHERE_DIR`
- `METASPHERE_SPAWN_NO_EXEC=1` to skip auto-exec (print manual command instead)

### Dependencies

`messages`, `metasphere-events`, `claude` binary.

---

## metasphere-wake

Wake (or re-task) a persistent agent — one whose directory contains `MISSION.md`. Starts a dedicated tmux session `metasphere-<name>` running claude in a respawn loop. Mirrors gateway's orchestrator session pattern.

### Subcommands

```
metasphere-wake @agent              # start (or attach status) the REPL
metasphere-wake @agent "task"       # start and inject an initial task
metasphere-wake --list              # list wakeable persistent agents
metasphere-wake --status            # show which are alive
metasphere-wake list                # alias
metasphere-wake status              # alias
```

### Behavior

- Waits up to 15 s polling `capture-pane` for "bypass permissions" to confirm the REPL is ready before injecting the first task.
- Sends `C-u` to clear stray input after `exec bash` transition.
- Uses `submit_to_tmux` from `metasphere-tmux-submit`.

### Files written

- `~/.metasphere/agents/@<name>/status` → `active: persistent session`
- `~/.metasphere/agents/@<name>/spawned_at`

### Dependencies

`tmux`, `claude`, `metasphere-tmux-submit`, `metasphere-events`.

---

## metasphere-session

Per-agent tmux session management used by `metasphere-agent spawn --interactive` and ad hoc starts. Similar to wake but builds a richer initial prompt from SOUL.md and task, and doesn't use a respawn loop.

### Subcommands

```
metasphere-session start|new @agent     # start interactive session (requires tmux)
metasphere-session attach|a @agent      # tmux attach
metasphere-session send|msg @agent "msg"   # inject via submit_to_tmux
metasphere-session list|ls              # list metasphere-* tmux sessions
metasphere-session stop|kill @agent     # graceful /exit then kill
```

Honors agent `sandbox` level for `--allowedTools`.

### Files read

- `~/.metasphere/agents/@<agent>/{scope,task,sandbox,SOUL.md}`

### Dependencies

`tmux`, `claude`, `metasphere-tmux-submit`.

---

## metasphere-gateway

Persistent orchestrator session daemon. Runs a watchdog loop, polls Telegram, injects user messages into the orchestrator's tmux session, and contains supervisor logic for stuck prompts and config-change restarts.

### Subcommands

```
metasphere-gateway daemon [interval]       # main loop (default 3s)
metasphere-gateway run ...                 # alias
metasphere-gateway inject "message"        # send to orchestrator session
metasphere-gateway send ...                # alias
metasphere-gateway ensure|start            # start session if needed
metasphere-gateway status|info             # session state, mode, telegram config
metasphere-gateway attach                  # tmux attach
metasphere-gateway restart-orchestrator    # kill+respawn the claude inside the orchestrator session
metasphere-gateway restart-claude          # alias
metasphere-gateway kill|stop               # kill tmux session
metasphere-gateway context ["message"]     # print per-message mode full context (debug)
```

### Modes

- `persistent` (default) — single long-running tmux session, messages injected into it
- `per-message` — fresh `claude -p` per message (used as fallback if tmux missing)

Set via `METASPHERE_SESSION_MODE`.

### Watchdog responsibilities

- Revive dead orchestrator session
- Stuck `[Pasted text #N` placeholder recovery (via `tmux_submit_watchdog`)
- Config-file mtime watch on settings.{json,local.json}, metasphere-context, metasphere-tmux-submit → `restart_claude_in_session`
- Auto-approve safety-hooks confirmation prompt (`check_stuck_prompts` sends "1"+Enter)
- Force-Enter stuck paste placeholder older than 15 s

### Harness hash baseline

`compute_harness_hash` sha256s CLAUDE.md + settings.{json,local.json} + metasphere-context and writes `~/.metasphere/state/harness_hash_baseline` on session start. `metasphere-context` re-computes per turn and surfaces "harness drift" if they differ — agent decides whether to `/exit`.

### Files read/written

- `~/.metasphere/config/telegram.env`, `telegram_chat_id`, `timezone`
- `~/.metasphere/gateway/pending_response`
- `~/.metasphere/telegram/offset`
- `~/.metasphere/logs/gateway.log`, `supervisor.log`
- `~/.metasphere/state/{last_config_mtime,stuck_paste_seen,last_safety_hook_intervention,harness_hash_baseline}`
- `~/.metasphere/agents/@orchestrator/{scope,status}`

### Env vars

- `METASPHERE_REPO_ROOT`, `METASPHERE_DIR`, `METASPHERE_SESSION_MODE`, `METASPHERE_TIMEZONE`

### Dependencies

`tmux`, `curl`, `jq`, `claude`, `metasphere-tmux-submit`, `metasphere-events`, `metasphere-telegram-stream`, `metasphere-telegram`, `metasphere-context` (indirectly), `tasks`.

---

## metasphere-tmux-submit

Library script (sourced) providing `submit_to_tmux <session> <message>` and `tmux_submit_watchdog <session>`. Handles the bracketed-paste Enter race by typing messages in literal mode (`send-keys -l`), sending `C-j` between lines and a single Enter at the end, then verifying via `capture-pane` that no `[Pasted text #` placeholder remains — retries Enter up to 3 times.

### Usage (library)

```
source metasphere-tmux-submit
submit_to_tmux <session> <message>
tmux_submit_watchdog <session>
```

### Usage (CLI)

```
echo "message" | metasphere-tmux-submit <session>
metasphere-tmux-submit watchdog <session>
```

### Env vars

- `TMUX_CMD` (optional; override tmux binary)

---

## metasphere-heartbeat

Proactive monitoring daemon. Checks for urgent unread messages, blocked/waiting agents, urgent tasks, and (optionally) invokes the orchestrator via claude for agent-driven heartbeats. Also polls Telegram bidirectionally.

### Subcommands

```
metasphere-heartbeat                       # single check (alias: once, check)
metasphere-heartbeat daemon [interval]     # combined daemon (default 30s heartbeat, 5s telegram poll)
metasphere-heartbeat notify "MSG"          # send manual Telegram notification
metasphere-heartbeat help
```

### Files read/written

- `~/.metasphere/heartbeat_state` (dedupe of notifications)
- `~/.metasphere/config/telegram.env`, `telegram_chat_id`
- `~/.metasphere/telegram_offset`
- `<repo>/.messages/inbox/*.msg`, `<repo>/.tasks/active/*.md`
- `~/.metasphere/agents/*/status`, `~/.metasphere/telegram/latest.json`

### Env vars

- `HEARTBEAT_INVOKE_AGENT=true` → invoke orchestrator per tick
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`

### Dependencies

`curl`, `jq`, `tmux`, `claude`, `metasphere-tmux-submit`, `messages`, `tasks`, `metasphere-telegram`.

---

## metasphere-schedule

Scheduling/cron system with two schemas:

- **Legacy interval/once**: `next_run` epoch + `repeat_secs`
- **New cron-format**: `kind=cron`, `cron_expr`, `tz`, `agent_id`, `payload_message` (imported from openclaw migration)

### Subcommands

```
metasphere-schedule                        # list (default)
metasphere-schedule list|ls
metasphere-schedule add "cmd" @TIME [@REPEAT]
metasphere-schedule new ...                # alias
metasphere-schedule remove|rm|delete ID
metasphere-schedule run|check              # execute due jobs (called by cron)
metasphere-schedule daemon [interval]      # loop cmd_run (default 60s)
metasphere-schedule message @target !label "msg" @TIME [@REPEAT]
metasphere-schedule msg ...                # alias
```

### Time formats

- `@HH:MM` — at that time today (or tomorrow if past)
- `@in:Nm` / `@in:Nh` — relative
- `@tomorrow` — +86400 s

### Repeat formats

- `@hourly`, `@daily`, `@weekly`
- `@every:Nm`, `@every:Nh`

### Cron matching

`cron_should_fire` prefers `python3 -c 'from croniter import croniter'` with `zoneinfo` (honors tz + DST) and a 180 s window. Falls back to bash `_cron_field_match` that handles `*`, `*/N`, `N-M`, `N,M,...`, exact values across the standard 5 fields (no named months/weekdays, no `@reboot`).

### Cron firing → agent resolution

Cron jobs map job `name` prefix to target persistent agents:

- `research-monitor:FOO` → `@research-FOO`
- `polymarket:*` → `@polymarket`
- `spot:autonomous-exploration*` → `@explorer`
- `rage-changelog*` → `@rage-changelog`
- `Morning briefing*` → `@briefing`

If target has `MISSION.md`, uses `metasphere-wake` to start + inject. Else falls back to `metasphere-spawn` or plain `messages send`.

### Files read/written

- `~/.metasphere/schedule/jobs.json`

### Dependencies

`jq`, `python3` + `croniter` (optional), `messages`, `metasphere-wake`, `metasphere-spawn`, `metasphere-agent`, `metasphere-gateway`, `metasphere`, `metasphere-events`.

---

## metasphere-events

Centralized JSONL event log.

### Subcommands

```
metasphere-events log|add|emit TYPE "msg" [--agent @name] [--scope /path] [--meta key=value]
metasphere-events tail|recent [N]                  # default 20
metasphere-events search|find|grep "pattern"
metasphere-events since|after 1h|30m|1d|"ISO8601"
metasphere-events context|ctx [N]                  # for orchestrator injection
metasphere-events stats|summary                    # by type, by agent
metasphere-events prune|clean [keep_days]          # default 7
```

### Event types in use

`agent.spawn`, `agent.sunset`, `agent.status`, `agent.session`, `agent.report`, `agent.wake`, `agent.heartbeat`, `task.create`, `task.start`, `task.complete`, `message.send`, `git.pre-commit`, `git.commit`, `git.checkout`, `git.push`, `migration.complete`, `project.init`, `supervisor.restart_claude`, `supervisor.auto_approve`, `supervisor.force_enter`, `watchdog.revive`, `watchdog.stuck_paste`, `schedule.cron_fire`, `user.message`.

### Event JSON schema

```json
{"id":"evt-<ts>-<pid>","timestamp":"...","type":"...","message":"...","agent":"@name","scope":"/path","meta":{}}
```

### Files written

- `~/.metasphere/events/events.jsonl`
- `~/.metasphere/events/index/<type>.log`
- `~/.metasphere/events/index/agents/<name>.log`

### Dependencies

`jq`, `metasphere-identity`.

---

## metasphere-trace

Capture command output for review agents.

### Subcommands

```
metasphere-trace run|exec "command"
metasphere-trace list|ls [--errors|-e] [--limit|-n N]
metasphere-trace show|get TRACE_ID
metasphere-trace search|find "pattern"
```

Detects errors via non-zero exit code or regex `error|failed|exception|fatal` in stdout/stderr. On error, sends messages to scope-local `@reviewer` and `@orchestrator`.

### Files written

- `~/.metasphere/traces/YYYY-MM-DD/HH-MM-SS-<cmd-slug>.{stdout,stderr,json}`
- `~/.metasphere/traces/index.jsonl`

### Env vars

- `METASPHERE_AGENT_ID` (default `@shell`), `METASPHERE_SCOPE`

### Dependencies

`jq`, `messages`.

---

## metasphere-fts

File search ranker ("poor man's BM25") backing the per-turn memory injection in `metasphere-context`.

### Usage

```
metasphere-fts "query string" [top_n]
```

Tokenizes the query (lowercase, alphanum, len≥3, stopword-filtered), runs a single `rg --type md -e '\b(tok1|tok2|...)\b'` over the corpus dirs, aggregates distinct-token matches per file in awk with `score = distinct*10 + hits/(hits+5)`, returns top N files with a 3-line snippet.

### Corpus

Default:

- `$REPO_ROOT/docs`, `scripts`, `.messages`, `.tasks`, `templates`
- `$METASPHERE_DIR/agents`

Override via `METASPHERE_FTS_CORPUS` (space-separated).

### Dependencies

`rg` (ripgrep), `awk`.

---

## metasphere-project

Project (directory with `.metasphere/`) management.

### Subcommands

```
metasphere-project init|new [path]
metasphere-project list|ls [--json]
metasphere-project status|show [name]
metasphere-project changelog|changes [name] [since|--since=1d]
metasphere-project learnings|learn [name]
```

### Files read/written

- `~/.metasphere/projects.json`
- `<path>/.metasphere/project.json`
- `<path>/.tasks/{active,completed}/*.md`
- `<path>/.changelog/YYYY-MM-DD.md`
- `<path>/.learnings/aggregated-YYYY-MM-DD.md`
- `~/.metasphere/events/events.jsonl` (read for task completions)
- `~/.metasphere/agents/@*/{scope,status,learnings/*.md}`

### Dependencies

`jq`, `git`, `metasphere-events`.

---

## metasphere-git-hooks

Install/uninstall git hooks that log events.

### Subcommands

```
metasphere-git-hooks install|add [repo-path]
metasphere-git-hooks uninstall|remove [repo-path]
metasphere-git-hooks status|check [repo-path]
```

Installs `pre-commit`, `post-commit`, `post-checkout`, `pre-push`. Hooks call `~/.metasphere/bin/metasphere-events log git.*` and (in post-commit) `cam index`. Backs up existing hooks to `<hook>.backup`.

### Dependencies

`git`, `metasphere-events` (expected at `~/.metasphere/bin/metasphere-events` from the hook's perspective).

---

## metasphere-migrate

Migrate configuration from OpenClaw (`~/.openclaw/`) to Metasphere.

### Subcommands

```
metasphere-migrate                              # defaults to detect
metasphere-migrate detect|check
metasphere-migrate run|migrate [--disable]
metasphere-migrate telegram|token               # extract telegram token only
metasphere-migrate sessions|cam                 # cam index
metasphere-migrate disable|stop [-y|--yes]      # disable openclaw gateway (interactive prompt by default)
```

### What `run` does

1. `migrate_telegram` — extract bot token from `~/.openclaw/openclaw.json` (tries `.channels.telegram.botToken`, `.telegram.botToken`, `.TELEGRAM_BOT_TOKEN`, `.telegram_bot_token`, `.env.TELEGRAM_BOT_TOKEN`, `.channels.telegram.tokenFile`), write `~/.metasphere/config/telegram.env`
2. `migrate_soul` — COPY `~/.openclaw/workspace/{SOUL,IDENTITY,USER,TOOLS,AGENTS,HEARTBEAT,MEMORY,BOOT,BOOTSTRAP}.md` into `~/.metasphere/agents/@orchestrator/` (never clobbers). Also migrates per-agent identities from `~/.openclaw/agents/<id>/` → `~/.metasphere/agents/@<id>/`. Writes `persona-index.md`.
3. `migrate_skills` — SYMLINK each `~/.openclaw/skills/<name>/` → `~/.metasphere/skills/<name>/`
4. `migrate_memory` — register `~/.openclaw/memory/main.sqlite` path in `~/.metasphere/config/openclaw_memory_db`; copy any legacy `.memory` / `memory.json` files
5. `migrate_cron` — transform `~/.openclaw/cron/jobs.json` → `~/.metasphere/schedule/jobs.json` (maps enabled cron-kind jobs; disabled/non-portable skipped; idempotent by `source_id`)
6. `parse_sessions` — run `cam index` if installed
7. Optionally disable launchd/systemd openclaw service

### Dependencies

`jq`, `curl`, `cam` (optional), `launchctl`/`systemctl`, `metasphere-events`.

---

## metasphere-telegram

Telegram bot command handler + long-polling daemon. Handles slash commands like `/status`, `/tasks`, `/agents`, `/send`, `/cam`, `/groups`, `/link`, `/help`, `/ping`, `/events`, `/tree`, `/messages`, `/spot`, `/inbox`. Non-slash input is routed to `@orchestrator` via the messages system.

### Subcommands

```
metasphere-telegram poll                   # long-poll daemon
metasphere-telegram process <chat_id> <text>   # dispatch a single slash command (called by gateway)
metasphere-telegram send "message"         # send to saved chat id
metasphere-telegram notify "message"       # send with "*Notification from @agent*" header
metasphere-telegram info                   # getMe JSON
```

### Slash commands routed

`/start`, `/status` (`/s`), `/agents` (`/a`), `/inbox [@agent]`, `/tasks` (`/t`), `/send @target !label msg`, `/cam query`, `/groups [create "Name"]`, `/link ["Name"]`, `/help` (`/h`), `/ping`, `/events`, `/tree`, `/messages` (`/m`), `/spot`.

### Files read/written

- `~/.metasphere/config/telegram.env`, `telegram_chat_id`, `telegram_forum_id`

### Env vars

- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`, `METASPHERE_AGENT_ID`

### Dependencies

`curl`, `jq`, `metasphere`, `messages`, `tasks`, `metasphere-events`, `metasphere-agent`, `metasphere-telegram-groups`, `cam`, `ssh` (for `/spot`).

---

## metasphere-telegram-stream

Telegram message archival + outbound send. Archives every received/sent message to `~/.metasphere/telegram/stream/YYYY-MM-DD.jsonl`, maintains `latest.json`, indexes into CAM, and injects incoming user messages directly into the orchestrator's tmux session (so turns fire without waiting for a heartbeat).

### Subcommands

```
metasphere-telegram-stream daemon [interval]       # poll continuously (default 5s)
metasphere-telegram-stream once|poll
metasphere-telegram-stream latest                  # print latest.json
metasphere-telegram-stream context [--history N]   # format recent conversation for agent context
metasphere-telegram-stream send "message"          # send from $METASPHERE_AGENT_ID, chunks at 3900 chars
metasphere-telegram-stream archive                 # show archive location
```

Outgoing `send` drops the `[orchestrator]` prefix for the orchestrator only; sub-agents get `*[name]*\n\n<text>`. Messages >3900 chars are split into `[n/N]` labeled chunks on paragraph/line boundaries. Default parse mode: plain text (no Markdown — avoids parse-entity failures). Non-command incoming messages trigger `inject_to_orchestrator` + a 👀 reaction.

### Files read/written

- `~/.metasphere/telegram/stream/YYYY-MM-DD.jsonl`
- `~/.metasphere/telegram/latest.json`
- `~/.metasphere/telegram/offset`
- `~/.metasphere/config/telegram_chat_id`

### Env vars

- `TELEGRAM_BOT_TOKEN`, `METASPHERE_AGENT_ID`

### Dependencies

`curl`, `jq`, `cam` (optional), `tmux`, `metasphere-tmux-submit`, `metasphere-telegram`.

---

## metasphere-telegram-groups

Telegram Forum (supergroup with topics) management.

### Subcommands

```
metasphere-telegram-groups setup
metasphere-telegram-groups create|new "Name" [emoji]
metasphere-telegram-groups list|ls
metasphere-telegram-groups send|msg TOPIC "message"
metasphere-telegram-groups link|url TOPIC
metasphere-telegram-groups workspace TYPE NAME [ID]   # type: project|task|agent
metasphere-telegram-groups process-cmd TOPIC_ID CMD ARGS
```

### Files read/written

- `~/.metasphere/telegram/groups/topics.json`
- `~/.metasphere/config/telegram_forum_id`
- `~/.metasphere/config/telegram.env` (sourced)

### Dependencies

`curl`, `jq`, `tasks`, `metasphere`.
