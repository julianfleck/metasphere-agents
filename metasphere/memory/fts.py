"""Token-overlap FTS strategy — pure stdlib port of scripts/metasphere-fts.

Walks a corpus of markdown files in well-known metasphere locations,
tokenizes the query (lowercase / alphanumeric / >=3 chars / drops a
small stopword set), and scores each file by the count of distinct
query tokens that appear, lightly weighted by hit count. The score is
normalized to 0..1 by dividing by the total query token count so the
top result is at most 1.0.

This is the deliberate behavioral twin of the bash version: same
corpus directories, same tokenization, same stopword list, same
distinct-token scoring. The bash awk produced ``distinct*10 + h/(h+5)``
which is monotonic in (distinct, hits); we keep the same ordering but
project to 0..1.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from ..paths import Paths, resolve
from .base import MemoryHit, MemoryStrategy

_STOPWORDS = frozenset(
    """the and for with this that from your you are was were have has will not
    but all any can had its into per via of to in on at is it as be by or if so
    we us an a""".split()
)

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_DEFAULT_TOP_N = 5


def _tokenize(query: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for tok in _TOKEN_RE.findall(query.lower()):
        if len(tok) < 3 or tok in _STOPWORDS or tok in seen:
            continue
        seen.add(tok)
        out.append(tok)
    return out


def _corpus_dirs(paths: Paths) -> list[Path]:
    override = os.environ.get("METASPHERE_FTS_CORPUS")
    if override:
        return [Path(p).expanduser() for p in override.split() if p]
    return [
        paths.repo / "docs",
        paths.repo / "scripts",
        paths.repo / ".messages",
        paths.repo / ".tasks",
        paths.repo / "templates",
        paths.root / "agents",
    ]


def _walk_md(dirs: list[Path]) -> list[Path]:
    files: list[Path] = []
    for d in dirs:
        if not d.is_dir():
            continue
        for root, _, names in os.walk(d):
            for n in names:
                if n.endswith(".md"):
                    files.append(Path(root) / n)
    return files


class TokenOverlapStrategy(MemoryStrategy):
    """Distinct-token-overlap scorer over markdown files in the metasphere corpus."""

    name = "fts"

    def __init__(self, paths: Paths | None = None) -> None:
        self._paths = paths

    def _resolve_paths(self) -> Paths:
        return self._paths or resolve()

    def search(self, query: str, limit: int = _DEFAULT_TOP_N) -> list[MemoryHit]:
        tokens = _tokenize(query)
        if not tokens:
            return []
        paths = self._resolve_paths()
        files = _walk_md(_corpus_dirs(paths))
        if not files:
            return []

        total_tokens = len(tokens)
        results: list[MemoryHit] = []
        for fp in files:
            try:
                text = fp.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            lower = text.lower()
            distinct = sum(1 for t in tokens if t in lower)
            if distinct == 0:
                continue
            # Find first matching line for the excerpt.
            best_line = ""
            for line in text.splitlines():
                low = line.lower()
                if any(t in low for t in tokens):
                    best_line = line.strip()
                    break
            # Hit count as weak tiebreaker.
            hits = sum(lower.count(t) for t in tokens)
            tiebreak = hits / (hits + 5.0)
            # Normalize to 0..1: distinct/total_tokens dominates,
            # tiebreak adds <0.05 so it never crosses a distinct boundary.
            score = (distinct / total_tokens) * 0.95 + tiebreak * 0.05
            if score > 1.0:
                score = 1.0
            try:
                rel = str(fp.relative_to(paths.repo))
            except ValueError:
                rel = str(fp)
            results.append(
                MemoryHit(
                    source=rel,
                    score=score,
                    excerpt=best_line[:200],
                    metadata={"distinct": distinct, "hits": hits, "strategy": "fts"},
                )
            )

        results.sort(key=lambda h: h.score, reverse=True)
        return results[:limit]
