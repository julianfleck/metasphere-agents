# Changelog

All notable changes to Metasphere Agents will be documented here.

---

## [2026-04-07T00:37:00Z] — Telegram Bridge + CAM Integration

**Context:** Human-in-the-loop via Telegram; user can intervene on every turn.

**Changes:**
- Created `metasphere-telegram` - Bot command handler (/status, /inbox, /tasks, /send, /cam)
- Created `metasphere-telegram-stream` - Stream archival + CAM indexing
- Created `metasphere-heartbeat` - Proactive monitoring daemon
- Updated `metasphere-context` to inject last Telegram message first
- Telegram messages archived to `~/.metasphere/telegram/stream/YYYY-MM-DD.jsonl`
- Messages indexed into CAM for searchable history

**Architecture:**
```
User (Telegram) ←→ metasphere-telegram-stream ←→ Agent Mesh
                          ↓
                    CAM (searchable)
                          ↓
                    Context Injection
```

**Features:**
- Bidirectional: human → agents, agents → human
- Last message always in agent context (user can intervene)
- Proactive notifications for urgent messages, blocked agents
- Stream archived locally + indexed to CAM

**Files created:**
- `scripts/metasphere-telegram`
- `scripts/metasphere-telegram-stream`
- `scripts/metasphere-heartbeat`

---

## [2026-04-07T00:20:00Z] — Self-Evolution Bootstrap

**Context:** Rewrote claude.md for operational self-evolution; session continued from context compaction.

**Changes:**
- Rewrote `claude.md` from specification doc to operational instructions
  - Added Evolution Loop based on Karpathy's AutoResearch pattern
  - Added SPIRAL cognitive loop documentation
  - Added Quick Reference for scripts, labels, priorities
  - Added Self-evolution protocol
- Updated @orchestrator identity files at `~/.metasphere/agents/@orchestrator/`
  - `LEARNINGS.md`: Session insights (fractal scoping, file-based coordination, hooks)
  - `MISSION.md`: Clear success criteria with phase checkboxes
  - `HEARTBEAT.md`: Current operational status
- Fixed hook path in `.claude/settings.json` (was pointing to wrong directory)
- Verified all scripts working: messages, tasks, metasphere-spawn, metasphere-context

**Learnings captured:**
1. Karpathy's AutoResearch: tight feedback loops > extensive planning
2. Fractal scoping with upward visibility creates natural information flow
3. File-based coordination beats API calls (git-friendly, inspectable, durable)
4. Context injection via hooks gives agents immediate awareness

**Files touched:** `claude.md`, `CHANGELOG.md`, `.claude/settings.json`, `~/.metasphere/agents/@orchestrator/*`

---

## [2026-04-06T23:30:00Z] — Renamed to Metasphere Agents

**Context:** Project renamed for clarity and installability on any machine.

**Changes:**
- Renamed from fractal-agents to metasphere-agents
- Runtime directory: `~/.metasphere/`
- Added installation instructions to claude.md
- Updated all CLI commands to use `metasphere-` prefix
- Prepared for GitHub remote at julianfleck/metasphere-agents

**Impact:** Project is now installable on any VM/computer.

**Files touched:** `claude.md`, `overview.yaml`, `CHANGELOG.md`

---

## [2026-04-06T23:15:00Z] — Added Git Versioning Backbone

**Context:** Git requested as backbone for tracking agent developments across machines.

**Changes:**
- Added comprehensive Git integration section to claude.md
- Defined auto-commit triggers (session_complete, summary_updated, decision_made, task_completed)
- Added git hooks for agent coordination (post-commit notifications)
- Specified merge strategies for concurrent agent work
- Integrated with CAM's existing GitHub sync mechanism

**Impact:** Enables full audit trail of agent activity with cross-machine sync.

**Files touched:** `claude.md`

---

## [2026-04-06T23:13:47Z] — Initial Project Bootstrap

**Context:** Anthropic cut OpenClaw API access; need lightweight replacement using Claude Code.

**Changes:**
- Created `claude.md` with full architecture specification
- Documented SPIRAL agentic loop (Sample → Pursue → Integrate → Reflect → Abstract → Loop)
- Defined virtual filesystem structure for agent/memory coordination
- Integrated Collective Agent Memory (CAM) for knowledge substrate
- Added Claude Code hook patterns (SessionStart, PreToolUse, Stop)
- Created directory structure (docs/, input/, .claude/)
- Wrote initial research notes with external sources
- Created `overview.yaml` project ledger

**Impact:** Project now has solid architectural foundation for MVP development.

**Files touched:** `claude.md`, `overview.yaml`, `docs/research/2026-04-06/01-initial-research.md`

---

## Research Sources

- [Multi-Agent Systems & AI Orchestration Guide 2026](https://www.codebridge.tech/articles/mastering-multi-agent-orchestration-coordination-is-the-new-scale-frontier)
- [Claude Code Hooks Reference](https://code.claude.com/docs/en/hooks)
- [Claude Agent SDK Hooks](https://platform.claude.com/docs/en/agent-sdk/hooks)
- ~/Code/collective-agent-memory (CAM architecture)
- ~/Code/writing/ (SPIRAL, semantic zooming, RAGE concepts)
