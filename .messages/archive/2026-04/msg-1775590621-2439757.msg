---
id: msg-1775590621-2439757
from: @orchestrator
to: @/
label: !task
status: unread
scope: /
created: 2026-04-07T19:37:01Z
read_at:
replied_at:
completed_at:
reply_to: 
---

TASK: Port scripts/metasphere-fts into metasphere/memory/ — a strategy-pluggable memory module wrapping CAM. EXECUTE NOW. python-rewrite branch. Commit your work.

DESIGN DIRECTIVE from Julian: "Fts should be a memory module that wraps cam and offers us a surface to implement different strategies"

So this is NOT a 1:1 port. The bash scripts/metasphere-fts is a single shell+rg+awk token-overlap scorer over recent context files. The Python version is the seed of a bigger memory subsystem with a clean strategy interface so we can plug in different recall mechanisms over time (token-overlap, embeddings, FTS5, CAM-backed semantic search, hybrid).

Read first:
1. /home/openclaw/Code/metasphere-agents/scripts/metasphere-fts (the source — read in full, ~120 lines)
2. /home/openclaw/Code/metasphere-agents/docs/PORTING.md fts section if any
3. /home/openclaw/.metasphere/config/openclaw_memory_db (the registered CAM sqlite path; use as the read-in-place CAM source)
4. /home/openclaw/Code/metasphere-agents/metasphere/io.py + paths.py + events.py + identity.py
5. Glance at scripts/metasphere-context to see how the fts hook is currently invoked from the per-turn context build

Verify branch python-rewrite first. Abort if not.

Build a new metasphere/memory/ subpackage:

metasphere/memory/__init__.py — public surface re-exports MemoryStrategy, MemoryHit, recall, default_strategies

metasphere/memory/base.py — abstract MemoryStrategy interface:
  - class MemoryHit (dataclass): source (str: file path | cam-session-id | "fts"), score (float 0..1), excerpt (str), metadata (dict)
  - class MemoryStrategy (Protocol or ABC):
      name: str (classvar)
      def search(self, query: str, limit: int = 5) -> list[MemoryHit]: ...
      def context_for(self, query: str, budget_chars: int = 2048) -> str: ...
        # convenience: search + format for context injection

metasphere/memory/fts.py — TokenOverlapStrategy(MemoryStrategy):
  - Replicates the bash metasphere-fts behavior: walks a corpus of context files (CLAUDE.md, recent .messages/inbox, recent .tasks/active, recent daily logs), tokenizes the query, scores documents by token-overlap (ratio of matched tokens to total query tokens), returns top N as MemoryHit list. Pure Python, no rg dependency — use re/str.split. Stdlib only.
  - Score threshold should match the bash version (which uses awk arithmetic). Default top_n=5.

metasphere/memory/cam.py — CamStrategy(MemoryStrategy):
  - Wraps the CAM CLI as a subprocess. The CAM cli is `cam` on PATH. Use `cam search "<query>" --limit N --json` if --json is available else parse the text output with the same scheme as scripts/metasphere-fts uses.
  - Each result becomes a MemoryHit with source=session-id, score from CAM, excerpt from preview field.
  - If `cam` binary is not on PATH, search returns empty list (and logs a warning ONCE per process via metasphere.events.log_event).
  - context_for falls back to the simple text formatter.

metasphere/memory/hybrid.py — HybridStrategy(MemoryStrategy):
  - Wraps a list of underlying strategies. search() unions their hits with score adjustment (cam hits scaled to 0..1, fts hits scaled to 0..1, weighted by an optional weights dict). Dedupes by (source, excerpt[:50]).
  - This is the seed of "different strategies" Julian asked for. Default weights: cam=0.6, fts=0.4.

metasphere/memory/api.py — top-level convenience:
  - default_strategies() -> list[MemoryStrategy]: returns [CamStrategy(), TokenOverlapStrategy()] (or HybridStrategy of both — pick one and document)
  - recall(query: str, limit: int = 5, strategies: list[MemoryStrategy] | None = None) -> list[MemoryHit]
  - context_for(query: str, budget_chars: int = 2048, strategies: ... | None = None) -> str: the entry point that metasphere/context.py will call instead of shelling out to scripts/metasphere-fts

Build /home/openclaw/Code/metasphere-agents/metasphere/cli/memory.py with:
  metasphere memory search "query" [--limit N] [--strategy fts|cam|hybrid]
  metasphere memory context "query" [--budget N]
  metasphere memory strategies   # list available strategies

Tests at /home/openclaw/Code/metasphere-agents/metasphere/tests/test_memory.py (use tmp_paths fixture):
- TokenOverlapStrategy returns at least one hit for a query that matches a known doc in tmp scope
- TokenOverlapStrategy scores 0..1 inclusive, top result has highest score
- CamStrategy gracefully handles missing cam binary (returns empty list, no exception)
- HybridStrategy unions hits from multiple strategies and dedupes
- recall(query) returns top-N as expected
- context_for(query, budget) respects the budget cap

Critical:
- This is a NEW abstraction, not a 1:1 port. The bash fts behavior must be preserved as the TokenOverlapStrategy default but the architecture must support plugging in new strategies later (embeddings, FTS5, vector DB, etc.) without touching the call sites.
- Stdlib only for fts.py. cam.py shells out to the cam CLI but does not import any CAM Python modules (CAM is external to metasphere).
- File reads under metasphere/io.py helpers where applicable.
- No state mutation in search() — strategies are read-only over the corpus.
- metasphere/context.py CAN be updated to call metasphere.memory.context_for instead of shelling out to scripts/metasphere-fts. Do that update as part of THIS commit, since it is a small atomic change. Update test_context.py if needed.

Final step:
- Run python -m pytest metasphere/tests/test_memory.py -v 2>&1 | tail -20 — must be all passing
- Run full suite: python -m pytest metasphere/tests/ -q 2>&1 | tail -3 — must be all passing
- Commit: git add metasphere/memory/ metasphere/cli/memory.py metasphere/tests/test_memory.py metasphere/context.py metasphere/tests/test_context.py && git commit -m "metasphere/memory: strategy-pluggable memory module wrapping cam + token-overlap fts" with body listing the strategies and the context.py update
- Write report at /home/openclaw/.metasphere/agents/@rewriter-memory/report.md
- Send: messages send @.. !done "rewriter-memory: memory subpackage + 3 strategies + cli + tests, NN passing, committed"

BEGIN. Use Read, Write, Edit, Bash. Do NOT modify scripts/.
