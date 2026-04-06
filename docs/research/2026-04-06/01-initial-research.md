# Initial Research: Fractal Agents MVP

**Timestamp:** 2026-04-06T23:13:47Z
**Researcher:** Claude (bootstrap session)

## Context

Bootstrap research for building a lightweight OpenClaw replacement using Claude Code. Anthropic cut API access to OpenClaw and third parties; need to build our own multi-agent harness.

## Queries (External Research)

1. "agentic loops multi-agent orchestration state of the art 2026"
2. "Claude Code CLI session injection hooks programmatic control 2026"

## Sources (External)

### Multi-Agent Orchestration (2026)

1. **[Multi-Agent Systems & AI Orchestration Guide 2026 | Codebridge](https://www.codebridge.tech/articles/mastering-multi-agent-orchestration-coordination-is-the-new-scale-frontier)** (accessed: 2026-04-06)
   - Key findings: Coordination is the new scale frontier; specialized agents > general-purpose

2. **[Orchestration Frameworks: LangChain, AutoGen, CrewAI](https://www.mhtechin.com/support/orchestration-frameworks-for-agentic-ai-langchain-autogen-crewai-the-complete-2026-guide/)** (accessed: 2026-04-06)
   - Key findings: Microsoft merged AutoGen + Semantic Kernel; LangGraph 2.2x faster than CrewAI

3. **[AI Agent Orchestration Frameworks 2026 | Catalyst & Code](https://www.catalystandcode.com/blog/ai-agent-orchestration-frameworks)** (accessed: 2026-04-06)
   - Key findings: Field going through "microservices revolution"; single agents → orchestrated teams

4. **[Deloitte: AI Agent Orchestration](https://www.deloitte.com/us/en/insights/industry/technology/technology-media-and-telecom-predictions/2026/ai-agent-orchestration.html)** (accessed: 2026-04-06)
   - Key findings: Market $8.5B by 2026, $35B by 2030; human-on-the-loop emerging

5. **[7 Agentic AI Trends 2026 | MLMastery](https://machinelearningmastery.com/7-agentic-ai-trends-to-watch-in-2026/)** (accessed: 2026-04-06)
   - Key findings: 1,445% surge in multi-agent inquiries Q1 2024 → Q2 2025

6. **[Agentic Lybic: Multi-Agent Execution System](https://arxiv.org/html/2509.11067v1)** (accessed: 2026-04-06)
   - Key findings: 57.07% success rate OSWorld benchmark; tiered reasoning + orchestration

### Claude Code Hooks

7. **[Claude Code Hooks Reference](https://code.claude.com/docs/en/hooks)** (accessed: 2026-04-06)
   - Key findings: 21 lifecycle events, 4 handler types, SessionStart for context injection

8. **[Claude Agent SDK Hooks](https://platform.claude.com/docs/en/agent-sdk/hooks)** (accessed: 2026-04-06)
   - Key findings: Full programmatic control; PreToolUse, PostToolUse, SubagentStart/Stop, Notification events; systemMessage injection

### Related Projects

9. **[claw-code (ultraworkers)](https://github.com/ultraworkers/claw-code)** (accessed: 2026-04-06)
   - Key findings: Rust CLI harness; token-overlap routing; session persistence; plugin architecture

10. **[memUBot (NevaMind)](https://github.com/NevaMind-AI/memUBot)** (accessed: 2026-04-06)
    - Key findings: Memory-first design; selective context transmission; auto-flush before compaction

11. **[AutoResearchClaw](https://github.com/aiming-lab/AutoResearchClaw)** (accessed: 2026-04-06)
    - Key findings: 23-stage pipeline; 4-layer citation verification; skills library; ACP bridge

## Internal Mapping

### Searched Internal Sources

- `~/Code/collective-agent-memory` - Full architecture analysis
- `~/Code/writing/concepts/2022-semantic-zooming/` - Zoom navigation patterns
- `~/Code/writing/concepts/2024-rage/` - RAGE architecture, diverge/converge/integrate
- `~/Code/writing/articles/2025-the-fractal-nature-of-information-processing/` - SPIRAL cognitive loop
- `~/Code/writing/articles/2025-how-we-may-think-we-think/` - Cognitive amplification
- `~/Code/writing/vocabulary/index.md` - Term definitions

### Key Internal Findings

**From Collective Agent Memory (CAM):**
- Session segmentation via embeddings (sentence-transformers)
- Keyword extraction (KeyBERT MMR)
- Entity extraction (GLiNER2 - 17 semantic types)
- SQLite FTS5 with BM25 scoring + recency boost
- Incremental indexing with daemon (warm models)
- GitHub sync for cross-machine persistence
- `cam context` command for session context injection

**From Semantic Zooming (2022):**
- Multi-level abstraction (verbatim → conceptual → bird's-eye)
- 3D navigation: X (lateral), Y (temporal), Z (depth/zoom)
- Progressive disclosure patterns
- Scale-invariant structure

**From RAGE (2024):**
- Frame semantics: typed knowledge units with slots
- Recursive structure: frames within frames
- Diverge → Converge → Integrate pattern
- Agentic retrieval loop with productive friction
- Operations registry for discoverable actions

**From Fractal Information Processing (2025):**
- SPIRAL cycle: Sample → Pursue → Integrate → Reflect → Abstract → Loop
- Scale-free networks with hub nodes
- Attractor basins as stable knowledge states
- Self-organized criticality (edge of chaos)

**From How We Think (2025):**
- Cognitive amplification > cognitive offloading
- Productive uncertainty keeps loops active
- Premature closure terminates inquiry too early
- Memory as recursive reconstruction, not retrieval
- Diffs over snapshots for semantic drift

## Synthesis

### Architecture Decision: Fractal Agents

Combine:
1. **CAM** for memory substrate (already built, production-ready)
2. **Claude Code hooks** for session injection and control
3. **Virtual filesystem** for agent/memory coordination
4. **SPIRAL loop** as the agentic execution pattern
5. **Git** as versioning backbone for tracking developments
6. **Progressive summarization** (semantic zooming) for memory organization

### Key Design Principles

1. **Diverge before converge** - Explore broadly, then synthesize
2. **Memory as first-class citizen** - CAM context injected every turn
3. **Filesystem as coordination primitive** - Agents read/write state as files
4. **Git as audit trail** - Every decision tracked with commits
5. **Human-on-the-loop** - Telegram escalation for uncertainty
6. **Recursive structure** - Same patterns at every scale (fractal)

### MVP Components

1. Git auto-commit on significant events
2. CAM context injection via SessionStart hook
3. Agent spawning via `claude -p` with context files
4. Status/output tracking via filesystem
5. Telegram notifications for human attention
6. Shell wrappers for virtual filesystem operations

## Open Questions

1. FUSE vs shell wrappers for virtual filesystem?
2. How to track agent lineage across sessions?
3. Memory pruning strategy for old segments?
4. Per-agent cost/budget limits?
5. Conflict resolution when agents disagree?

## Proposed Actions

1. Create `claude.md` with full architecture spec ✅
2. Set up directory structure ✅
3. Implement git auto-commit hooks
4. Create `fractal-inject-context` script
5. Create `fractal-spawn-agent` script
6. Test basic agent spawning with CAM context
