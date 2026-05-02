"""Tests for AutoMemoryStrategy — MEMORY.md + linked files recall."""

from __future__ import annotations

from pathlib import Path

from metasphere.memory.auto import AutoMemoryStrategy, _default_memory_root


def _setup_memory(tmp_path: Path) -> Path:
    """Create a minimal MEMORY.md + linked files under tmp_path."""
    root = tmp_path / "memory"
    root.mkdir()

    (root / "MEMORY.md").write_text(
        "- [Tmux injection fix](tmux_fix.md) — fixed the interleaving bug\n"
        "- [Telegram auth notes](telegram_auth.md) — token exchange design\n"
        "- [Noise entry](noise.md) — irrelevant content about cooking\n"
        "- [Missing file](does_not_exist.md) — broken link\n"
        "- [Escape attempt](../../../etc/passwd) — path traversal\n",
        encoding="utf-8",
    )

    (root / "tmux_fix.md").write_text(
        "---\nname: tmux injection fix\ntype: project\n---\n"
        "Fixed the tmux send-keys interleaving bug where concurrent "
        "writers braided characters into the orchestrator pane. "
        "The fix uses a per-pane fcntl lock around submit_to_tmux.\n",
        encoding="utf-8",
    )

    (root / "telegram_auth.md").write_text(
        "---\nname: Telegram auth\ntype: reference\n---\n"
        "Token exchange for telegram user authorization. "
        "Each new contact must present a token to register. "
        "Address book maps chat_id to authorized user.\n",
        encoding="utf-8",
    )

    (root / "noise.md").write_text(
        "---\nname: Noise\ntype: user\n---\n"
        "My favorite recipe for banana bread involves flour eggs "
        "and sugar. Nothing about software engineering here.\n",
        encoding="utf-8",
    )

    return root


def test_search_ranks_tmux_file_first_for_tmux_query(tmp_path):
    root = _setup_memory(tmp_path)
    strat = AutoMemoryStrategy(root=root)
    hits = strat.search("tmux injection interleaving submit")
    assert len(hits) >= 1
    assert hits[0].source == "auto-memory:tmux_fix.md"
    assert hits[0].score > 0


def test_search_ranks_telegram_for_telegram_query(tmp_path):
    root = _setup_memory(tmp_path)
    strat = AutoMemoryStrategy(root=root)
    hits = strat.search("telegram authorization token exchange")
    assert len(hits) >= 1
    assert hits[0].source == "auto-memory:telegram_auth.md"


def test_search_skips_broken_links(tmp_path):
    root = _setup_memory(tmp_path)
    strat = AutoMemoryStrategy(root=root)
    hits = strat.search("does not exist missing file")
    sources = {h.source for h in hits}
    assert "auto-memory:does_not_exist.md" not in sources


def test_search_blocks_path_traversal(tmp_path):
    root = _setup_memory(tmp_path)
    strat = AutoMemoryStrategy(root=root)
    hits = strat.search("etc passwd escape")
    sources = {h.source for h in hits}
    assert not any("passwd" in s for s in sources)


def test_search_empty_query_returns_empty(tmp_path):
    root = _setup_memory(tmp_path)
    strat = AutoMemoryStrategy(root=root)
    assert strat.search("") == []
    assert strat.search("   ") == []


def test_search_strips_frontmatter_from_excerpts(tmp_path):
    root = _setup_memory(tmp_path)
    strat = AutoMemoryStrategy(root=root)
    hits = strat.search("tmux injection fix")
    assert hits
    # Excerpt should NOT contain frontmatter delimiters
    assert "---" not in hits[0].excerpt
    assert "name:" not in hits[0].excerpt
    # But should contain the actual body
    assert "interleaving" in hits[0].excerpt


def test_search_respects_limit(tmp_path):
    root = _setup_memory(tmp_path)
    strat = AutoMemoryStrategy(root=root)
    hits = strat.search("the", limit=1)
    assert len(hits) <= 1


def test_missing_memory_md_returns_empty(tmp_path):
    strat = AutoMemoryStrategy(root=tmp_path)
    assert strat.search("anything") == []


def test_default_memory_root_fallback_has_no_operator_name(tmp_path, monkeypatch):
    # Force the function past the PWD-derived branch and the iterdir
    # scan so the last-resort fallback runs. Stranger installs land
    # here whenever ~/.claude/projects/ either doesn't exist or holds
    # no child with memory/MEMORY.md. Guard against any operator name
    # creeping into the shipped fallback slug. Inspect only the
    # function-controlled suffix (relative to HOME) so the assertion
    # ignores any operator names that happen to live in the test
    # runner's tmp prefix.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("PWD", "")
    fallback = _default_memory_root()
    suffix = fallback.relative_to(tmp_path).as_posix().lower()
    for needle in ("openclaw", "julian", "j0lian", "ella"):
        assert needle not in suffix, f"fallback slug leaks {needle!r}: {suffix}"
