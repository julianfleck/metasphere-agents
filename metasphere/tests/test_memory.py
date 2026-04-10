"""Tests for metasphere.memory."""

from __future__ import annotations

from pathlib import Path

import pytest

from metasphere.memory import (
    CamStrategy,
    HybridStrategy,
    MemoryHit,
    TokenOverlapStrategy,
    context_for,
    recall,
)
from metasphere.memory.fts import _tokenize
from metasphere.paths import Paths


def _seed_doc(paths: Paths, rel: str, body: str) -> Path:
    p = paths.project_root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")
    return p


# ---------- tokenizer ----------


def test_tokenize_drops_stopwords_and_short():
    toks = _tokenize("The Quick brown fox in a metasphere")
    assert "the" not in toks
    assert "in" not in toks
    assert "quick" in toks
    assert "brown" in toks
    assert "metasphere" in toks


# ---------- TokenOverlapStrategy ----------


def test_token_overlap_returns_hit_for_matching_doc(tmp_paths: Paths):
    _seed_doc(tmp_paths, "docs/alpha.md", "the quokka loves alpine ferns\n")
    _seed_doc(tmp_paths, "docs/beta.md",  "irrelevant content about pandas\n")
    strat = TokenOverlapStrategy(tmp_paths)
    hits = strat.search("quokka alpine", limit=5)
    assert hits, "expected at least one hit"
    assert any("alpha.md" in h.source for h in hits)


def test_token_overlap_score_in_unit_range_and_top_first(tmp_paths: Paths):
    _seed_doc(tmp_paths, "docs/a.md", "quokka alpine fern\n")  # 3 distinct
    _seed_doc(tmp_paths, "docs/b.md", "quokka only here\n")    # 1 distinct
    strat = TokenOverlapStrategy(tmp_paths)
    hits = strat.search("quokka alpine fern", limit=5)
    assert len(hits) == 2
    for h in hits:
        assert 0.0 <= h.score <= 1.0
    assert hits[0].score >= hits[1].score
    assert "a.md" in hits[0].source


def test_token_overlap_empty_query_returns_nothing(tmp_paths: Paths):
    _seed_doc(tmp_paths, "docs/a.md", "anything\n")
    assert TokenOverlapStrategy(tmp_paths).search("the and for", limit=5) == []


# ---------- CamStrategy ----------


def test_cam_strategy_missing_binary_returns_empty(tmp_paths: Paths, monkeypatch):
    # Force shutil.which to return None.
    monkeypatch.setattr("metasphere.memory.cam.shutil.which", lambda _b: None)
    # Reset the once-warned flag so the test is independent.
    import metasphere.memory.cam as cam_mod
    cam_mod._CAM_MISSING_WARNED = False
    strat = CamStrategy()
    assert strat.search("anything", limit=3) == []


# ---------- HybridStrategy ----------


class _StubStrategy(TokenOverlapStrategy):
    def __init__(self, name: str, hits: list[MemoryHit]):
        self.name = name
        self._hits = hits

    def search(self, query: str, limit: int = 5) -> list[MemoryHit]:
        return list(self._hits[:limit])


def test_hybrid_unions_and_dedupes():
    a = _StubStrategy("fts", [
        MemoryHit(source="docs/a.md", score=1.0, excerpt="quokka alpine"),
        MemoryHit(source="docs/b.md", score=0.5, excerpt="other"),
    ])
    b = _StubStrategy("cam", [
        MemoryHit(source="docs/a.md", score=1.0, excerpt="quokka alpine"),  # dupe
        MemoryHit(source="cam/x", score=0.8, excerpt="cam-only"),
    ])
    hyb = HybridStrategy([a, b], weights={"fts": 0.4, "cam": 0.6})
    hits = hyb.search("quokka", limit=10)
    sources = [h.source for h in hits]
    # dedupes a.md, keeps b.md and cam/x
    assert sources.count("docs/a.md") == 1
    assert "cam/x" in sources
    assert "docs/b.md" in sources


# ---------- recall + context_for ----------


def test_recall_returns_top_n(tmp_paths: Paths):
    _seed_doc(tmp_paths, "docs/a.md", "quokka alpine fern\n")
    _seed_doc(tmp_paths, "docs/b.md", "quokka only\n")
    _seed_doc(tmp_paths, "docs/c.md", "alpine peaks\n")
    hits = recall(
        "quokka alpine",
        limit=2,
        strategies=[TokenOverlapStrategy(tmp_paths)],
    )
    assert len(hits) == 2
    assert "a.md" in hits[0].source


def test_context_for_respects_budget(tmp_paths: Paths):
    for i in range(20):
        _seed_doc(tmp_paths, f"docs/d{i}.md", "quokka alpine\n")
    out = context_for(
        "quokka alpine",
        budget_chars=200,
        strategies=[TokenOverlapStrategy(tmp_paths)],
    )
    assert len(out) <= 250  # budget + final newline slack
    assert "quokka" in out or "###" in out
