"""AutoMemoryStrategy — search the orchestrator's hand-curated MEMORY.md index.

Reads ``MEMORY.md``, follows each ``[title](file.md)`` link to its target,
token-overlaps the combined body against the query, and returns ranked
:class:`MemoryHit` objects. Highest-signal recall source for the
orchestrator: each entry is a distilled feedback/project/user/reference
memo from a past incident.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from .base import MemoryHit, MemoryStrategy

_LINK_RE = re.compile(r"\[([^\]]+)\]\(([^)]+\.md)\)")


def _default_memory_root() -> Path:
    """Locate the auto-memory directory under ``~/.claude/projects/``."""
    home = Path(os.environ.get("HOME", "~")).expanduser()
    pwd = os.environ.get("PWD", "")
    if pwd:
        slug = "-" + pwd.replace("/", "-")
        candidate = home / ".claude" / "projects" / slug / "memory"
        if candidate.is_dir():
            return candidate
    base = home / ".claude" / "projects"
    if base.is_dir():
        for child in sorted(base.iterdir()):
            mem = child / "memory"
            if (mem / "MEMORY.md").is_file():
                return mem
    return home / ".claude" / "projects" / "_no_memory" / "memory"


def _tokenize(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]{3,}", s.lower())}


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            return text[end + 5:]
    return text


class AutoMemoryStrategy(MemoryStrategy):
    """Reads MEMORY.md + each linked ``*.md``, ranks by token overlap."""

    name = "auto-memory"

    def __init__(self, root: Path | None = None) -> None:
        self.root = root or _default_memory_root()

    def search(self, query: str, limit: int = 5) -> list[MemoryHit]:
        index = self.root / "MEMORY.md"
        if not index.is_file():
            return []
        q_tokens = _tokenize(query)
        if not q_tokens:
            return []
        try:
            index_text = index.read_text(encoding="utf-8")
        except OSError:
            return []

        hits: list[MemoryHit] = []
        for match in _LINK_RE.finditer(index_text):
            title, rel_path = match.group(1), match.group(2)
            mem_file = (self.root / rel_path).resolve()
            try:
                mem_file.relative_to(self.root.resolve())
            except ValueError:
                continue
            if not mem_file.is_file():
                continue
            try:
                body = mem_file.read_text(encoding="utf-8")
            except OSError:
                continue
            d_tokens = _tokenize(body)
            if not d_tokens:
                continue
            overlap = len(q_tokens & d_tokens)
            if overlap == 0:
                continue
            score = overlap / max(len(q_tokens), 1)
            excerpt = _strip_frontmatter(body).strip()[:400]
            hits.append(MemoryHit(
                source=f"auto-memory:{rel_path}",
                score=score,
                excerpt=excerpt,
                metadata={"title": title, "path": str(mem_file)},
            ))

        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[:limit]
