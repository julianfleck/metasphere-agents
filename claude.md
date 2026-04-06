# Metasphere Agents

> A lightweight OpenClaw replacement using Claude Code (`claude -p`) with shared knowledge substrates, progressive summarization, and the SPIRAL agentic loop.

**Last Updated:** 2026-04-06T23:30:00Z
**Status:** MVP Specification (Divergence Phase)
**Runtime:** `~/.metasphere/`

---

## Installation

```bash
# Clone the repo
git clone https://github.com/julianfleck/metasphere-agents.git
cd metasphere-agents

# Install to ~/.metasphere (creates bin/, config/, agents/, memory/, queue/)
./install.sh

# Add to PATH (or add to your shell profile)
export PATH="$HOME/.metasphere/bin:$PATH"

# Verify installation
metasphere status
```

### Requirements

- **Claude Code CLI** (`claude` command)
- **Git** (for versioning backbone)
- **Python 3.10+** (for CAM - `pip install collective-agent-memory`)
- **Optional:** Telegram CLI for human-in-the-loop notifications

---

## Project Vision

Build a **fractal multi-agent harness** that:
1. Uses Claude Code CLI as the execution engine (`claude -p`)
2. Implements shared knowledge substrates via Collective Agent Memory (CAM)
3. Exposes a virtual filesystem interface for agent/memory coordination
4. Follows the **SPIRAL** (diverge → converge → integrate) agentic loop
5. Enables proactive human-in-the-loop via Telegram notifications
6. Supports cron-based autonomous agent scheduling
7. **Installs anywhere** - any VM or computer via `~/.metasphere/`

**"Fractal"** = Multiple nested layers of agent/knowledge with progressive summarization at each level. Like zooming in/out on a map—same patterns repeat at every scale.

---

## Core Architecture

### The SPIRAL Agentic Loop

Every agent follows this recursive cycle (from Julian's cognitive framework):

```
┌─────────────────────────────────────────────────────────────┐
│                    SPIRAL CYCLE                             │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ┌──────────┐    ┌──────────┐    ┌───────────┐             │
│  │  SAMPLE  │ → │  PURSUE  │ → │ INTEGRATE │             │
│  │ (Extract)│    │ (Expand) │    │   (Map)   │             │
│  └──────────┘    └──────────┘    └───────────┘             │
│       ↑                                   ↓                 │
│       │                                   │                 │
│  ┌──────────┐                      ┌───────────┐           │
│  │   LOOP   │ ←────────────────── │  REFLECT  │           │
│  │(Recurse) │                      │ (Evaluate)│           │
│  └──────────┘                      └───────────┘           │
│       ↑                                   ↓                 │
│       │         ┌───────────┐            │                 │
│       └──────── │  ABSTRACT │ ←──────────┘                 │
│                 │ (Compress)│                               │
│                 └───────────┘                               │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

**Phase Definitions:**

| Phase | Action | Agent Behavior |
|-------|--------|----------------|
| **Sample** (Extract) | Pull signals from environment | Read CAM context, check notifications, scan filesystem |
| **Pursue** (Expand) | **DIVERGE** - Explore broadly | Spawn sub-agents, web search, generate hypotheses |
| **Integrate** (Map) | Relate to existing knowledge | Search CAM for related work, build context maps |
| **Reflect** (Evaluate) | Assess fit and quality | Quality gates, confidence scoring, human escalation |
| **Abstract** (Compress) | **CONVERGE** - Synthesize | Distill insights, update summaries, prune connections |
| **Loop** (Recurse) | **INTEGRATE** - Feed back | Update CAM, trigger next cycle, evolve schemas |

**Key Insight:** Each SPIRAL changes what the next SPIRAL notices. Progressive refinement, not linear execution.

---

## Virtual Filesystem Architecture

Expose agent state and memory as a navigable filesystem:

```
~/.metasphere/
├── agents/                          # Active agent instances
│   ├── @orchestrator/               # Main coordinator
│   │   ├── status                   # Current state (running|idle|waiting)
│   │   ├── context.md               # Injected context for this agent
│   │   ├── task                     # Current task description
│   │   ├── output/                  # Agent outputs
│   │   └── subagents/               # Child agents
│   │       ├── @researcher/
│   │       └── @implementer/
│   │
│   └── @<agent-name>/               # Any spawned agent
│       ├── status
│       ├── context.md
│       ├── task
│       └── output/
│
├── memory/                          # Knowledge substrate (CAM integration)
│   ├── <project>/
│   │   ├── summary.md               # Top-level project summary
│   │   ├── learnings.md             # Extracted patterns
│   │   ├── decisions.md             # Key decisions made
│   │   └── topics/
│   │       └── <topic>/
│   │           ├── summary.md       # Topic-level summary
│   │           ├── segments/        # Raw CAM segments
│   │           └── entities.json    # Extracted entities
│   │
│   └── active/                      # Currently active work
│       └── <project>/
│           └── tasks/
│               ├── active/
│               │   └── <task-name>/
│               │       ├── summary.md
│               │       ├── context.md
│               │       └── progress.json
│               ├── planned/
│               └── completed/
│
├── queue/                           # Task coordination
│   ├── pending/                     # Tasks awaiting assignment
│   ├── claimed/                     # Tasks being worked
│   └── review/                      # Tasks needing human review
│
└── config/                          # System configuration
    ├── agents.yaml                  # Agent definitions
    ├── cron.yaml                    # Scheduled tasks
    └── hooks.yaml                   # Claude Code hooks
```

### Filesystem Operations

```bash
# Check agent status
cat ~/.metasphere/agents/@researcher/status
# → "running: analyzing codebase structure"

# Tail agent output in real-time
tail -f ~/.metasphere/agents/@researcher/output/log

# Search memory for related work
grep "authentication" ~/.metasphere/memory/*/learnings.md

# Get project summary at any zoom level
cat ~/.metasphere/memory/myproject/summary.md                    # High-level
cat ~/.metasphere/memory/myproject/topics/auth/summary.md        # Topic-level
cat ~/.metasphere/memory/myproject/topics/auth/segments/001.md   # Detail-level

# Queue a task for any available agent
echo "Research JWT best practices 2026" > ~/.metasphere/queue/pending/task-001

# Signal human attention needed
touch ~/.metasphere/queue/review/decision-needed-auth-approach
```

---

## Progressive Summarization (Semantic Zooming)

Memory organized in zoom levels:

```
LEVEL 0 (Zoomed Out): Project essence
  "Auth system with JWT + refresh rotation"
     ↓
LEVEL 1: Domain summaries
  "Authentication: JWT with 15-min access, 7-day refresh, Redis blacklist"
     ↓
LEVEL 2: Topic summaries
  "Middleware validates tokens, extracts user, handles expiry gracefully"
     ↓
LEVEL 3: Segment summaries
  "Session on 2026-04-06: Implemented token refresh with rotation"
     ↓
LEVEL 4 (Zoomed In): Raw conversation segments
  Full CAM segments with timestamps, entities, keywords
```

**Navigation:**
- **Vertical (Z-axis):** Zoom in/out through abstraction levels
- **Horizontal (X-axis):** Traverse related topics at same granularity
- **Temporal (Y-axis):** Move through time within a topic

---

## Agent Lifecycle

### 1. Spawning an Agent

```python
# Orchestrator spawns a sub-agent
spawn_agent(
    name="@researcher",
    task="Analyze authentication patterns in codebase",
    context=[
        "~/.metasphere/memory/active/project/tasks/active/auth/context.md",
        "cam context 'authentication patterns'"  # Inject CAM search
    ],
    parent="@orchestrator",
    hooks={
        "on_complete": notify_parent,
        "on_stuck": escalate_human,
        "on_discovery": update_memory
    }
)
```

### 2. Agent Bootstrap (Every Session Start)

Each agent receives:

1. **System Context:** Project-level claude.md rules (this file)
2. **CAM Context:** `cam context` output for relevant topics
3. **Task Context:** Specific task from `~/.metasphere/agents/@name/task`
4. **Parent Context:** Summary of what parent agent knows
5. **History:** Relevant segments from previous related work

### 3. Agent Communication

Agents coordinate through:

1. **Filesystem signals:** Write to `~/.metasphere/agents/@name/status`
2. **CAM updates:** Segments auto-indexed, searchable by all agents
3. **Queue system:** Drop tasks in `~/.metasphere/queue/pending/`
4. **Direct handoff:** Write to `~/.metasphere/agents/@target/context.md`

### 4. Human-in-the-Loop

Agents escalate via Telegram when:
- Confidence < 0.7 on critical decision
- Task blocked for > 10 minutes
- Explicit uncertainty: "I need clarification on..."
- Cost threshold exceeded

```bash
# Agent triggers human attention
telegram-notify "[@researcher] Decision needed: Use Redis or PostgreSQL for session storage? Context: ~/.metasphere/queue/review/session-storage-decision"
```

---

## Integration with Claude Code

### Session Injection via Hooks

Use Claude Code's hook system for context injection:

```json
// .claude/settings.json
{
  "hooks": {
    "SessionStart": [{
      "matcher": "",
      "command": "metasphere-inject-context"
    }],
    "PreToolUse": [{
      "matcher": "Write|Edit",
      "command": "metasphere-audit-changes"
    }],
    "Stop": [{
      "matcher": "",
      "command": "metasphere-persist-session"
    }]
  }
}
```

### `metasphere-inject-context` Script

```bash
#!/bin/bash
# Inject CAM context and agent state at session start

# Get agent identity from environment
AGENT_ID="${METASPHERE_AGENT_ID:-@default}"

# Build context injection
cat << EOF
{
  "systemMessage": "$(cat ~/.metasphere/agents/$AGENT_ID/context.md | jq -Rs .)",
  "continue": true
}
EOF
```

### Programmatic Agent Spawning

```bash
# Spawn a new agent with claude -p
claude -p "$(cat << 'EOF'
You are @researcher, a specialized research agent.

## Your Task
$(cat ~/.metasphere/agents/@researcher/task)

## Context
$(cam context "related topics" --best)

## Rules
1. Update ~/.metasphere/agents/@researcher/status frequently
2. Write discoveries to ~/.metasphere/agents/@researcher/output/
3. Signal completion by writing "complete" to status
4. Escalate uncertainty via Telegram hook
EOF
)" --allowedTools "Read,Grep,Glob,WebSearch,WebFetch" \
   --output-dir ~/.metasphere/agents/@researcher/output/
```

---

## CAM Integration

### Memory Search (Front and Center)

Every agent interaction starts with:

```bash
# Inject relevant context
cam context "topic keywords" --json | jq '.context'

# Search for related work
cam search "authentication middleware" --limit 5

# Get recent activity
cam recent --hours 24 --agent claude
```

### Automatic Indexing

Sessions automatically indexed by CAM daemon:
- Topic segmentation via embeddings
- Keyword extraction (KeyBERT)
- Entity extraction (GLiNER2 - 17 types)
- FTS5 indexing with BM25 scoring
- Recency boosting (exponential decay)

### Cross-Agent Knowledge Sharing

```python
# Agent A discovers a pattern
cam_index_segment(
    content="JWT refresh tokens should rotate on each use",
    keywords=["jwt", "refresh", "rotation", "security"],
    entities={"concept": ["JWT", "token rotation"]},
    agent="@researcher"
)

# Agent B later searches
results = cam_search("refresh token rotation")
# → Finds Agent A's discovery
```

---

## Git as Version Control Backbone

Git provides the versioning infrastructure for tracking all agent activity:

### Directory Structure (Git-Managed)

The `~/.metasphere/` directory is itself a git repository for cross-machine sync:

```
~/.metasphere/                      # Git-tracked runtime directory
├── .git/                           # Standard Git
├── bin/                            # CLI tools (added to PATH)
├── config/                         # Configuration (agents.yaml, cron.yaml, git.yaml)
├── memory/                         # CAM syncs here (git-tracked)
│   ├── sessions/                   # All session segments
│   └── summaries/                  # Progressive summaries
├── agents/                         # Agent state (git-tracked)
│   └── @<name>/
│       ├── history/                # Commit log of agent decisions
│       └── outputs/                # Work products
├── queue/                          # Task management
└── logs/                           # System and agent logs
```

### Automatic Git Operations

Every significant agent action triggers git commits:

```yaml
# ~/.metasphere/config/git.yaml
auto_commit:
  triggers:
    - event: "session_complete"
      message: "[@{agent}] Session: {task_summary}"
      paths: ["memory/sessions/", "agents/@{agent}/"]

    - event: "summary_updated"
      message: "[memory] Updated {level} summary for {topic}"
      paths: ["memory/summaries/"]

    - event: "decision_made"
      message: "[@{agent}] Decision: {decision_summary}"
      paths: ["agents/@{agent}/history/"]

    - event: "task_completed"
      message: "[@{agent}] Completed: {task_name}"
      paths: ["queue/completed/"]

  auto_push:
    enabled: true
    remote: "origin"
    branch: "main"
    interval: 300  # Push every 5 minutes if changes exist

  merge_strategy:
    conflicts: "theirs"  # Agent outputs don't conflict semantically
    squash_sessions: false  # Keep granular history
```

### Git Hooks for Agent Coordination

```bash
# .git/hooks/post-commit
#!/bin/bash
# Notify other agents of changes

CHANGED_FILES=$(git diff-tree --no-commit-id --name-only -r HEAD)

# If memory was updated, signal relevant agents
if echo "$CHANGED_FILES" | grep -q "^memory/"; then
  metasphere-notify-agents "memory_updated" "$CHANGED_FILES"
fi

# If agent state changed, update dashboard
if echo "$CHANGED_FILES" | grep -q "^agents/"; then
  metasphere-refresh-dashboard
fi
```

### Session-to-Commit Mapping

Each Claude Code session maps to a git branch or series of commits:

```bash
# Start a new task - create feature branch
git checkout -b agent/@researcher/task-auth-analysis

# Agent works, auto-commits accumulate
# [@researcher] Session: Analyzed JWT patterns in auth/
# [@researcher] Session: Found refresh token vulnerability
# [@researcher] Decision: Recommend token rotation

# Task complete - merge to main
git checkout main
git merge --no-ff agent/@researcher/task-auth-analysis \
  -m "[@researcher] Completed: Authentication security analysis"
git push origin main
```

### Cross-Machine Sync (Like CAM)

```bash
# Pull latest from all agents before starting work
metasphere-sync pull

# Internal:
git fetch origin
git merge origin/main --strategy-option=theirs

# After session, push changes
metasphere-sync push

# Internal:
git add -A
git commit -m "[@{agent}] Auto-sync $(date -u +%Y-%m-%dT%H:%M:%SZ)"
git push origin main
```

### Viewing Agent History

```bash
# See all decisions by a specific agent
git log --oneline --author="@researcher" -- agents/@researcher/history/

# See evolution of a summary over time
git log -p -- memory/summaries/auth/summary.md

# Find when a pattern was first discovered
git log --all --source -S "JWT rotation" -- memory/

# Diff between two points in time
git diff HEAD~20..HEAD -- memory/summaries/
```

### Conflict Resolution

When multiple agents work concurrently:

```yaml
# Merge strategy per path
merge_rules:
  "memory/sessions/*":
    strategy: "union"  # Append-only, no conflicts possible

  "memory/summaries/*":
    strategy: "latest"  # Most recent summary wins
    notify_on_overwrite: true

  "agents/*/outputs/*":
    strategy: "keep_both"  # Rename conflicting files

  "queue/*":
    strategy: "union"  # Tasks are independent
```

### Git + CAM Integration

CAM already supports GitHub sync. Metasphere extends this:

```bash
# CAM_SYNC_REPO becomes the fractal repo
export CAM_SYNC_REPO="user/metasphere-memory"

# CAM sessions go to memory/sessions/
# Metasphere adds agent state, queue, config

# Single source of truth for:
# - Session segments (CAM)
# - Progressive summaries (Metasphere)
# - Agent decisions (Metasphere)
# - Task history (Metasphere)
```

---

## Cron & Autonomous Operation

### Agent Scheduling

```yaml
# ~/.metasphere/config/cron.yaml
schedules:
  - name: "daily-memory-maintenance"
    cron: "0 3 * * *"  # 3 AM daily
    agent: "@memory-curator"
    task: "Prune low-value segments, update summaries, check for conflicts"

  - name: "weekly-learning-extraction"
    cron: "0 9 * * 1"  # Monday 9 AM
    agent: "@pattern-extractor"
    task: "Analyze past week's sessions, extract patterns, update learnings.md"

  - name: "continuous-research-monitor"
    cron: "*/30 * * * *"  # Every 30 min
    agent: "@news-watcher"
    task: "Check for relevant updates in tracked topics"
    notify_on_discovery: true
```

### Spot-Like Container Setup

For running on a dedicated VM (like Spot):

```bash
# Container deployment structure
metasphere-agents/                  # This repo
├── Containerfile                   # Docker/nspawn build
├── install.sh                      # Installs to ~/.metasphere/
├── systemd/
│   ├── metasphere-orchestrator.service
│   ├── metasphere-memory-daemon.service
│   └── metasphere-cron.timer
├── nginx/
│   └── metasphere-dashboard.conf
└── scripts/                        # Copied to ~/.metasphere/bin/
    ├── metasphere
    ├── metasphere-inject-context
    ├── metasphere-spawn-agent
    ├── metasphere-commit
    ├── metasphere-sync
    └── metasphere-notify
```

---

## Implementation Phases

### Phase 1: MVP Core (This Sprint)

1. **Git backbone** - auto-commit on session complete, sync across machines
2. **CAM integration** - context injection on every turn
3. **Basic agent spawning** via `claude -p`
4. **Session persistence** - hooks to persist/restore state
5. **Telegram notifications** for human escalation
6. **Virtual filesystem** - shell wrappers (FUSE optional)

### Phase 2: Orchestration

1. **Orchestrator agent** - manages sub-agents
2. **Task queue** - filesystem-based coordination
3. **Progressive summarization** - automatic zoom levels
4. **Cross-agent memory** - shared discoveries

### Phase 3: Autonomy

1. **Cron scheduling** - autonomous background agents
2. **Self-improvement** - agents propose rule updates
3. **Dashboard** - web UI for monitoring
4. **Container deployment** - Spot-like setup

---

## Key Dependencies

| Component | Source | Purpose |
|-----------|--------|---------|
| Claude Code CLI | Anthropic | Agent execution engine |
| CAM | ~/Code/collective-agent-memory | Memory search & indexing |
| Git | System | Version control backbone |
| FUSE (optional) | libfuse | Virtual filesystem |
| Telegram CLI | telegram-cli | Human notifications |
| systemd | System | Cron & service management |

---

## Design Principles

### From Julian's Cognitive Framework

1. **Recursive Loops:** Outputs become inputs. Each SPIRAL refines the next.
2. **Productive Uncertainty:** Don't close loops prematurely. Maintain exploration.
3. **Attractors:** Let stable patterns form naturally in memory.
4. **Semantic Zooming:** Navigate abstraction levels fluidly.
5. **Diverge Before Converge:** Explore broadly, then synthesize.

### From State of the Art (2026)

1. **Feedback Loops:** Verifier agents catch errors before propagation
2. **Hierarchical Orchestration:** CEO → Manager → Worker pattern
3. **Human-on-the-Loop:** Progressive autonomy based on task criticality
4. **Specialized Agents:** Single-purpose agents > general-purpose
5. **Token Efficiency:** Minimize context, maximize relevance

### From Claude Code Hooks

1. **SessionStart:** Inject context at session initialization
2. **PreToolUse:** Audit and control tool access
3. **SubagentStart/Stop:** Track parallel task spawning
4. **Notification:** Forward status to external systems
5. **systemMessage:** Inject guidance into conversation

---

## Migration from OpenClaw

| OpenClaw Feature | Metasphere Equivalent |
|------------------|----------------------|
| Gateway | Claude Code CLI + hooks |
| Skills | claude.md rules + MCP servers |
| Sessions | CAM segments + ~/.metasphere/agents/ |
| Cron | systemd timers + cron.yaml |
| Memory | CAM with progressive summarization |
| Container | systemd-nspawn (Spot pattern) |

---

## Open Questions

1. **FUSE vs Shell Wrappers:** Full virtual filesystem or command aliases?
2. **Agent Identity:** How to track agent lineage across sessions?
3. **Memory Pruning:** When/how to garbage collect old segments?
4. **Cost Control:** Per-agent budget limits via hooks?
5. **Conflict Resolution:** When agents disagree, who wins?

---

## References

### Research Sources (2026-04-06)

- [Multi-Agent Systems & AI Orchestration Guide 2026](https://www.codebridge.tech/articles/mastering-multi-agent-orchestration-coordination-is-the-new-scale-frontier)
- [Orchestration Frameworks for Agentic AI: LangChain, AutoGen, CrewAI](https://www.mhtechin.com/support/orchestration-frameworks-for-agentic-ai-langchain-autogen-crewai-the-complete-2026-guide/)
- [AI Agent Orchestration Frameworks in 2026](https://www.catalystandcode.com/blog/ai-agent-orchestration-frameworks)
- [Deloitte: Unlocking exponential value with AI agent orchestration](https://www.deloitte.com/us/en/insights/industry/technology/technology-media-and-telecom-predictions/2026/ai-agent-orchestration.html)
- [7 Agentic AI Trends to Watch in 2026](https://machinelearningmastery.com/7-agentic-ai-trends-to-watch-in-2026/)
- [Claude Code Hooks Reference](https://code.claude.com/docs/en/hooks)
- [Claude Agent SDK Hooks](https://platform.claude.com/docs/en/agent-sdk/hooks)

### Internal References

- ~/Code/collective-agent-memory - CAM source
- ~/Code/writing/concepts/2022-semantic-zooming/ - Zoom navigation
- ~/Code/writing/concepts/2024-rage/ - RAGE architecture, diverge/converge
- ~/Code/writing/articles/2025-the-metasphere-nature-of-information-processing/ - SPIRAL
- ~/Code/writing/articles/2025-how-we-may-think-we-think/ - Cognitive loops
- ~/Code/writing/vocabulary/index.md - Term definitions

### Related Projects

- [claw-code (ultraworkers)](https://github.com/ultraworkers/claw-code) - Rust CLI harness
- [memUBot (NevaMind)](https://github.com/NevaMind-AI/memUBot) - Memory-first agent
- [AutoResearchClaw](https://github.com/aiming-lab/AutoResearchClaw) - Research pipeline

---

## Changelog

### 2026-04-06T23:30:00Z - Renamed to Metasphere Agents

**Context:** Project renamed for clarity; made installable on any VM/computer.

**Changes:**
- Renamed from fractal-agents to metasphere-agents
- Changed runtime directory to `~/.metasphere/`
- Added installation instructions
- Updated all paths and CLI commands to use `metasphere-` prefix
- Added bin/, logs/ to directory structure

**Status:** Ready for development.

---

### 2026-04-06T23:15:00Z - Added Git Versioning Backbone

**Context:** Git as the backbone for tracking agent developments.

**Changes:**
- Added comprehensive Git integration section
- Defined auto-commit triggers per event type
- Added git hooks for agent coordination
- Specified merge strategies for concurrent agents
- Integrated with CAM's existing GitHub sync

---

### 2026-04-06T23:13:47Z - Initial Specification

**Context:** Bootstrap project after Anthropic cut OpenClaw API access.

**Changes:**
- Created initial spec with full architecture
- Documented SPIRAL agentic loop
- Defined virtual filesystem structure
- Integrated CAM and Claude Code hooks
- Outlined migration path from OpenClaw
