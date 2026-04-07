"""Top-level convenience surface for the memory subpackage.

Callers (notably ``metasphere.context``) should use ``recall`` and
``context_for`` rather than instantiating strategies directly. This
keeps the choice of default strategy stack a single edit away.
"""

from __future__ import annotations

from .base import MemoryHit, MemoryStrategy
from .cam import CamStrategy
from .fts import TokenOverlapStrategy
from .hybrid import HybridStrategy


def default_strategies() -> list[MemoryStrategy]:
    """The default stack: a single hybrid wrapping cam + fts.

    Returned as a list so callers can extend or replace it; the hybrid
    is the only entry by default because it already merges its inputs.
    """
    return [HybridStrategy([CamStrategy(), TokenOverlapStrategy()])]


def _resolve(strategies: list[MemoryStrategy] | None) -> list[MemoryStrategy]:
    return list(strategies) if strategies is not None else default_strategies()


def recall(
    query: str,
    limit: int = 5,
    strategies: list[MemoryStrategy] | None = None,
) -> list[MemoryHit]:
    """Return the top-``limit`` hits across all configured strategies."""
    if not query.strip():
        return []
    merged: dict[tuple[str, str], MemoryHit] = {}
    for strat in _resolve(strategies):
        try:
            hits = strat.search(query, limit=limit)
        except Exception:
            continue
        for h in hits:
            key = (h.source, h.excerpt[:50])
            existing = merged.get(key)
            if existing is None or h.score > existing.score:
                merged[key] = h
    out = sorted(merged.values(), key=lambda h: h.score, reverse=True)
    return out[:limit]


def context_for(
    query: str,
    budget_chars: int = 2048,
    strategies: list[MemoryStrategy] | None = None,
) -> str:
    """Format recall results as a markdown block capped at ``budget_chars``."""
    hits = recall(query, limit=10, strategies=strategies)
    if not hits:
        return ""
    out: list[str] = []
    used = 0
    for h in hits:
        block = f"### {h.source}  (score: {h.score:.3f})\n    {h.excerpt}\n"
        if used + len(block) > budget_chars:
            break
        out.append(block)
        used += len(block)
    return "".join(out).rstrip() + "\n" if out else ""
