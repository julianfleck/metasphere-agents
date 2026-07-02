"""Weighted union of multiple memory strategies.

The seed of "different strategies" recall: ask each underlying
strategy, scale its hits by a per-strategy weight, dedupe by
``(source, excerpt[:50])``, and return the merged top-N.
"""

from __future__ import annotations

from .base import MemoryHit, MemoryStrategy

DEFAULT_WEIGHTS = {"cam": 0.6, "fts": 0.4}


class HybridStrategy(MemoryStrategy):
    name = "hybrid"

    def __init__(
        self,
        strategies: list[MemoryStrategy],
        weights: dict[str, float] | None = None,
    ) -> None:
        self._strategies = list(strategies)
        self._weights = dict(weights) if weights is not None else dict(DEFAULT_WEIGHTS)

    def search(self, query: str, limit: int = 5) -> list[MemoryHit]:
        merged: dict[tuple[str, str], MemoryHit] = {}
        for strat in self._strategies:
            try:
                hits = strat.search(query, limit=limit)
            except Exception:
                continue
            w = self._weights.get(strat.name, 1.0 / max(len(self._strategies), 1))
            for h in hits:
                scaled = h.score * w
                key = (h.source, h.excerpt[:50])
                existing = merged.get(key)
                if existing is None or scaled > existing.score:
                    merged[key] = MemoryHit(
                        source=h.source,
                        score=scaled,
                        excerpt=h.excerpt,
                        metadata={**h.metadata, "via": strat.name},
                    )
        out = sorted(merged.values(), key=lambda h: h.score, reverse=True)
        return out[:limit]
