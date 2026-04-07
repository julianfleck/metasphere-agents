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
    """Return the top-``limit`` hits across all configured strategies.

    Multiple strategies are merged through a single :class:`HybridStrategy`
    so the dedupe + weighting policy is owned in exactly one place.
    Passing a single strategy short-circuits the wrap.
    """
    if not query.strip():
        return []
    resolved = _resolve(strategies)
    if not resolved:
        return []
    if len(resolved) == 1:
        merger = resolved[0]
    else:
        merger = HybridStrategy(resolved)
    try:
        return merger.search(query, limit=limit)
    except Exception:
        return []


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
