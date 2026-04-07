"""Strategy-pluggable memory module.

Wraps CAM and a stdlib token-overlap FTS behind a uniform
``MemoryStrategy`` interface so callers (notably ``metasphere.context``)
can request "recall" without knowing which backend served the result.
New strategies (embeddings, FTS5, vector DB, hybrid retrievers) drop in
without touching call sites.
"""

from __future__ import annotations

from .api import context_for, default_strategies, recall
from .base import MemoryHit, MemoryStrategy
from .cam import CamStrategy
from .fts import TokenOverlapStrategy
from .hybrid import HybridStrategy

__all__ = [
    "MemoryHit",
    "MemoryStrategy",
    "TokenOverlapStrategy",
    "CamStrategy",
    "HybridStrategy",
    "recall",
    "context_for",
    "default_strategies",
]
