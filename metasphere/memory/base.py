"""Abstract surface for memory strategies.

A ``MemoryStrategy`` is anything that maps a free-text query to a list
of ``MemoryHit`` records. Strategies are read-only over their corpus —
``search`` MUST NOT mutate state.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from typing import Any


@dataclass
class MemoryHit:
    """A single recall result."""

    source: str          # file path | cam-session-id | strategy-defined handle
    score: float         # normalized 0..1
    excerpt: str         # short human-readable preview
    metadata: dict[str, Any] = field(default_factory=dict)


class MemoryStrategy(abc.ABC):
    """Abstract base for any pluggable recall backend."""

    name: str = "base"

    @abc.abstractmethod
    def search(self, query: str, limit: int = 5) -> list[MemoryHit]:
        """Return up to ``limit`` hits for ``query``, best-first."""
