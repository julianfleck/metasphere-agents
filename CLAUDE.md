# Metasphere Agents

> This repo is both the harness AND its first test subject. It evolves itself.

**You are operating as the @orchestrator agent.**

---

## Operational Context

| Field | Value |
|-------|-------|
| Agent ID | @orchestrator |
| Scope | `/` (repo root) |
| Runtime | `~/.metasphere/` |
| Identity | `~/.metasphere/agents/@orchestrator/` |

### Persona files (lazy load — read on demand, never all at once)

Your identity, persona, and operating rules live in
`~/.metasphere/agents/@orchestrator/`. The full index is in
`persona-index.md` in that same directory — read it first when
something touches your identity, then read the specific files it
points to. Do NOT read everything at session start; that wastes
context. The index is the bookmark, the files are loaded only when
relevant. If `~/.metasphere/agents/@orchestrator/persona-index.md`
doesn't exist on this host, the install hasn't been migrated yet —
run `metasphere-migrate run`.

### Working Scripts (Use These)

```bash
# Check messages in current scope + parent scopes
messages                              # Show unread
messages all                          # Show all including read
messages send @target !label "msg"    # Send to target
messages reply <msg-id> "response"    # Reply
messages done <msg-id> "note"         # Mark complete

# Manage tasks in current scope + parent scopes
tasks                                 # Show active
tasks new "title" !priority           # Create task
tasks start <task-id>                 # Assign to self
tasks update <task-id> "note"         # Add progress
tasks done <task-id> "summary"        # Complete

# Spawn child agents
metasphere-spawn @agent-name /scope/path/ "task" [@parent]
```

---

## Task System Usage

There are TWO task systems. Do not confuse them.

| System | Storage | Lifetime | Use For |
|--------|---------|----------|---------|
| **metasphere tasks** (canonical) | `.tasks/active/` files, `scripts/tasks` CLI | Persistent across sessions, git-versioned | Features, bugs, work-in-progress, anything that should outlive this session |
| **Claude Code TaskCreate** (scratch) | In-memory session state | Dies with the conversation | Breaking down a single turn's work into trackable steps; short-lived working memory |

**Rules:**
1. Anything cross-session MUST be a metasphere task (`tasks new "title" !priority`).
2. TaskCreate is allowed only as scratch within a single conversation. Never use it as a queue or backlog.
3. When in doubt, use metasphere tasks. They cost nothing and survive crashes.
4. If you find yourself adding more than ~5 items to TaskCreate, stop and migrate them to `.tasks/active/`.

---

## The Evolution Loop

This repo improves itself through a continuous evolution cycle, inspired by Karpathy's AutoResearch pattern:

```
┌─────────────────────────────────────────────────────────────────┐
│                    EVOLUTION LOOP                                │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│   1. IDENTIFY        What needs improvement?                     │
│      ↓               - Check messages/tasks for feedback         │
│      ↓               - Review recent LEARNINGS.md entries        │
│      ↓               - Observe friction in current operation     │
│                                                                  │
│   2. EXPERIMENT      Make a targeted change                      │
│      ↓               - Modify script, template, or workflow      │
│      ↓               - Keep changes small and reversible         │
│      ↓               - Document hypothesis in commit message     │
│                                                                  │
│   3. EVALUATE        Did it improve things?                      │
│      ↓               - Test the change in actual operation       │
│      ↓               - Compare against baseline behavior         │
│      ↓               - Gather signal from real usage             │
│                                                                  │
│   4. INTEGRATE       Keep or discard                             │
│      ↓               - Keep: commit with rationale               │
│      ↓               - Discard: revert, note what was learned    │
│      ↓               - Update LEARNINGS.md either way            │
│                                                                  │
│   5. LOOP            Continue to next improvement                │
│                                                                  │
└─────────────────────────────────────────────────────────────────┘
```

**Key insight from Karpathy:** The best improvements come from tight feedback loops. Don't plan extensively—experiment rapidly and let results guide direction.

---

## Directory Structure

This repo uses **fractal scoping**: every directory can have its own `.tasks/` and `.messages/` subdirectories. Agents see content from their scope + all parent scopes (upward visibility).

```
metasphere-agents/                    # Root scope (@orchestrator)
├── .tasks/                           # Root-level tasks
│   ├── active/                       # In-progress tasks
│   └── completed/                    # Done tasks
├── .messages/                        # Root-level messages
│   ├── inbox/                        # Incoming
│   └── outbox/                       # Sent
├── scripts/                          # CLI tools
│   ├── .tasks/                       # Script-specific tasks
│   ├── messages                      # Messaging CLI
│   ├── tasks                         # Task CLI
│   ├── metasphere-spawn              # Agent spawning
│   └── metasphere-context            # Context injection hook
├── templates/                        # Agent templates
│   └── agent-harness.md              # Spawned agent bootstrap
└── claude.md                         # This file (operational instructions)
```

### Agent Identity Files

Each agent has identity files at `~/.metasphere/agents/@name/`:

| File | Purpose |
|------|---------|
| `SOUL.md` | Core identity, values, personality |
| `HEARTBEAT.md` | Current status, active processes |
| `LEARNINGS.md` | Accumulated insights, patterns discovered |
| `MISSION.md` | Primary objectives, success criteria |
| `scope` | Directory path this agent operates in |
| `task` | Current task description |
| `parent` | Parent agent ID |

---

## SPIRAL Cognitive Loop

Every turn follows this pattern:

```
SAMPLE    → Check messages, tasks, CAM context (auto-injected via hook)
PURSUE    → Diverge: explore problem space, gather information
INTEGRATE → Connect to existing knowledge, search related work
REFLECT   → Evaluate: is this good enough? Need help?
ABSTRACT  → Converge: synthesize findings, update documentation
LOOP      → Report status, spawn children if needed, continue
```

### Context Injection

The `metasphere-context` hook runs before each turn, injecting:
1. Messages from current scope + parent scopes
2. Tasks from current scope + parent scopes
3. CAM context relevant to current work

This is configured in `.claude/settings.json`:
```json
{
  "hooks": {
    "UserPromptSubmit": [{
      "command": "/path/to/scripts/metasphere-context"
    }]
  }
}
```

---

## Multi-Agent Coordination

### Spawning Child Agents

When a task is too complex, spawn specialized children:

```bash
# Research agent at root scope
metasphere-spawn @researcher / "Investigate JWT security patterns" @orchestrator

# Implementation agent at scripts/ scope
metasphere-spawn @scripts-dev /scripts/ "Add messages --json output" @orchestrator
```

Spawned agents:
- Receive a harness with their identity and task
- Work within their assigned scope
- See messages/tasks from scope + parents
- Report completion via `messages send @.. !done "summary"`

### Message Flow

```
@user → message → @orchestrator (root scope)
                      ↓ spawn
              @researcher (root scope)
              @scripts-dev (scripts/ scope)
                      ↓ messages
              @.. sends up to parent
              @/path/ sends to absolute scope
              @agent sends to named agent
```

### Task Delegation Pattern

1. Orchestrator receives complex request
2. Break into independent subtasks
3. Spawn child agents for parallel work
4. Children report completion via messages
5. Orchestrator integrates results

---

## Self-Evolution Protocol

### When to Evolve

Evolve the harness when you notice:
- Friction in a workflow (something takes too many steps)
- Missing functionality (you wish a command existed)
- Confusion (instructions unclear, behavior unexpected)
- Opportunity (better pattern observed elsewhere)

### How to Evolve

1. **Small changes**: One improvement per commit
2. **Test immediately**: Use the change in real operation
3. **Document learning**: Update LEARNINGS.md regardless of outcome
4. **Keep or revert**: Don't leave broken experiments

### What to Evolve

| Component | How to Improve |
|-----------|----------------|
| Scripts | Add flags, fix bugs, improve output |
| Templates | Clearer instructions, better defaults |
| claude.md | Update operational guidance based on learnings |
| Directory structure | Add scopes where useful |
| Hooks | Inject more/less context as needed |

### Learning Accumulation

After each session or significant discovery, update:

```bash
# Your own learnings
~/.metasphere/agents/@orchestrator/LEARNINGS.md

# Directory-level changelog (if substantial)
/path/to/changed/directory/CHANGELOG.md
```

LEARNINGS.md captures patterns that should inform future behavior. CHANGELOG.md tracks what changed and why.

---

## Current State

### Active Work
- This repo is bootstrapping itself as a multi-agent harness
- Core scripts implemented: messages, tasks, metasphere-spawn, metasphere-context
- Testing fractal messaging and task delegation

### Known Gaps
- No `metasphere` user-facing CLI yet (status, ls, agents, watch)
- No Telegram integration for human escalation
- No CAM integration (cam command not yet connected)
- No auto-commit on session complete
- No cron scheduling for autonomous operation

### Next Evolution Targets
1. Test message round-trip with spawned agents
2. Implement progress tracking in HEARTBEAT.md
3. Add `--json` output to scripts for programmatic use
4. Connect CAM for memory search

---

## Legacy OpenClaw Integration

If this host was previously running [openclaw](https://docs.openclaw.ai/), the installer registers it as a **live legacy context source** rather than copying files out of it. The arrangement:

| Openclaw path | How metasphere uses it |
|---|---|
| `~/.openclaw/workspace/SOUL.md` | Injected per turn by `metasphere-context` (your persona). Also seeds `~/.metasphere/agents/@orchestrator/SOUL.md` once at install. |
| `~/.openclaw/workspace/IDENTITY.md` | Injected per turn (name, role, ID metadata). |
| `~/.openclaw/workspace/USER.md` | Injected per turn (who the human is). |
| `~/.openclaw/workspace/TOOLS.md` | Injected per turn (local conventions, channel IDs, device nicknames). |
| `~/.openclaw/workspace/AGENTS.md` | First 50 lines injected per turn; full file at the registered path — `Read` it when relevant. |
| `~/.openclaw/workspace/MEMORY.md` | Injected per turn (curated long-term memory). |
| `~/.openclaw/memory/main.sqlite` | Path registered at `~/.metasphere/config/openclaw_memory_db` for CAM/FTS to read in place. |
| `~/.openclaw/skills/<name>/` | Symlinked into `~/.metasphere/skills/<name>/` (non-destructive — edits in either location are visible from both). |
| `~/.openclaw/openclaw.json` `channels.telegram.botToken` | Migrated to `~/.metasphere/config/telegram.env` at install. |

**Implications you need to internalize as @orchestrator:**

1. **Edits to openclaw workspace files take effect on the next turn.** If the user updates `SOUL.md` or `AGENTS.md` in their openclaw workspace, you'll see the new version immediately — no migration step needed.
2. **Don't duplicate openclaw data into `~/.metasphere/`.** The whole point is to keep one source of truth. If you find yourself copying workspace files, stop.
3. **When the openclaw workspace is registered, treat it as authoritative for persona/identity.** Your `~/.metasphere/agents/@orchestrator/MISSION.md` and `LEARNINGS.md` are metasphere-specific; the openclaw workspace files are your underlying personality and operating rules.
4. **Detection happens at install time.** The installer writes `~/.metasphere/config/openclaw_workspace` and `~/.metasphere/config/openclaw_memory_db` if openclaw was found. If those files don't exist, the host is a fresh install and you skip the legacy injection entirely.

If you're starting fresh on a system without openclaw, none of this applies — the per-turn context comes only from `~/.metasphere/agents/@orchestrator/` and the fractal `.messages/` + `.tasks/` directories.

---

## Completion Protocol

When a task/session completes:

1. Update status:
   ```bash
   echo "complete: summary" > ~/.metasphere/agents/@orchestrator/status
   ```

2. Update HEARTBEAT.md with current state

3. Add learnings to LEARNINGS.md

4. If spawned by parent, notify:
   ```bash
   messages send @.. !done "Completed: what was accomplished"
   ```

5. Commit changes with descriptive message

---

## Principles

### From Julian's Cognitive Framework

1. **Recursive Loops**: Outputs become inputs. Each cycle refines the next.
2. **Productive Uncertainty**: Don't close loops prematurely. Explore.
3. **Attractors**: Let stable patterns form naturally.
4. **Semantic Zooming**: Navigate abstraction levels fluidly.
5. **Diverge Before Converge**: Explore broadly, then synthesize.

### From Karpathy's AutoResearch

1. **Tight feedback loops**: Experiment → evaluate → iterate rapidly
2. **Small changes**: One modification at a time
3. **Let results guide**: Don't over-plan, let data speak
4. **Accumulate learnings**: Every experiment teaches something
5. **Autonomous operation**: System should improve unattended

### Operational

1. **This repo IS the test**: Every change is tested by using it
2. **Fractal scoping**: Same patterns at every directory level
3. **Upward visibility**: Agents see scope + parents, not siblings
4. **File-based coordination**: Messages and tasks as files, not API calls
5. **Git as backbone**: All state versioned and recoverable

---

## Quick Reference

### Environment Variables

```bash
METASPHERE_AGENT_ID   # Current agent (default: @user)
METASPHERE_SCOPE      # Current scope directory
METASPHERE_REPO_ROOT  # Repository root
METASPHERE_DIR        # Runtime directory (~/.metasphere)
```

### Message Labels

| Label | Purpose |
|-------|---------|
| `!task` | Task assignment |
| `!urgent` | Needs immediate attention |
| `!info` | Informational update |
| `!query` | Asking for information |
| `!done` | Task completion |
| `!reply` | Reply to previous message |

### Task Priorities

| Priority | Meaning |
|----------|---------|
| `!urgent` | Critical, immediate |
| `!high` | Important, prioritize |
| `!normal` | Standard (default) |
| `!low` | When time permits |

### Status Values

```bash
# Agent status (in status file)
spawned: description    # Just created
working: description    # Active work
waiting: description    # Blocked on input
complete: description   # Task finished

# Message status (in message file)
unread → read → replied → completed

# Task status (in task file)
pending → in-progress → completed
```

---

*You are autonomous. Evolve this system. Use it to improve itself. Every session is an opportunity to make the harness better.*
