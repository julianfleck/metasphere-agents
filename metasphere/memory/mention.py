"""Project-mention detection for deterministic memory injection (Stage A).

Step 0 of internal design notes (threading
the user's prompt into context) shipped in PR #183. This module is Stage A:
the deterministic, zero-threshold trigger that replaces fuzzy-relevance gating
as the *primary* memory-injection path.

When the user names a project, we want to ALWAYS inject that project's memory
capsule — no relevance threshold to mis-tune (the 0.35→0.65 threshold bump is
exactly why "No relevant memory found" shows on most turns). Detection is a
precompiled, case-insensitive, word-bounded alternation over the canonical
project names from the registry plus an alias map. O(len(prompt)); negligible.

Public surface:
    detect_mentioned_projects(prompt, paths=None, *, limit=3) -> list[Project]

Alias sources (merged, both optional — names alone work for most projects):
  * an ``aliases: [...]`` list on the project's registry entry (colocated), and
  * a central ``~/.metasphere/project-aliases.yaml`` mapping ``name -> [aliases]``.
Separator variants ("mesa-chat" ↔ "mesa.chat" ↔ "mesa chat") are matched
automatically, so most projects need no alias config at all.
"""

from __future__ import annotations

import logging
import re
from typing import Optional

from ..paths import Paths, resolve
from ..project import Project, _load_registry

logger = logging.getLogger(__name__)

#: Tokens this short are too noisy to match as bare project names (they'd hit
#: inside ordinary prose). Explicit aliases bypass nothing — they must still
#: clear this floor, so a one-letter alias is silently ignored rather than
#: flooding every turn with false positives.
_MIN_TOKEN_LEN = 3

#: Characters treated as interchangeable word-internal separators. A project
#: named ``mesa-chat`` should match "mesa chat", "mesa.chat", "mesachat".
_SEP_CLASS = r"[-_.\s]*"
_SEP_CHARS = "-_. "


def _aliases_path(paths: Paths):
    return paths.root / "project-aliases.yaml"


def _load_central_aliases(paths: Paths) -> dict[str, list[str]]:
    """Load ``~/.metasphere/project-aliases.yaml`` (``name -> [aliases]``).

    Missing file / unparseable YAML / unexpected shape all degrade to an
    empty map — alias config is a convenience, never load-bearing.
    """
    path = _aliases_path(paths)
    try:
        raw = path.read_text(encoding="utf-8")
    except (FileNotFoundError, NotADirectoryError):
        return {}
    except OSError as exc:  # pragma: no cover - unexpected fs error
        logger.warning("could not read %s: %s", path, exc)
        return {}
    try:
        import yaml

        data = yaml.safe_load(raw) or {}
    except Exception as exc:  # noqa: BLE001 - malformed config must not break recall
        logger.warning("could not parse %s: %s", path, exc)
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, list[str]] = {}
    for name, aliases in data.items():
        if isinstance(aliases, str):
            aliases = [aliases]
        if isinstance(aliases, list):
            out[str(name)] = [str(a) for a in aliases if str(a).strip()]
    return out


def _token_variant(token: str) -> Optional[str]:
    """Compile a single match token into a separator-flexible regex fragment.

    ``"mesa-chat"`` → ``mesa[-_.\\s]*chat``. Returns ``None`` for tokens
    whose alphanumeric content is shorter than :data:`_MIN_TOKEN_LEN`.
    """
    alnum = re.sub(r"[^0-9A-Za-z]", "", token)
    if len(alnum) < _MIN_TOKEN_LEN:
        return None
    pieces = [p for p in re.split(f"[{re.escape(_SEP_CHARS)}]+", token) if p]
    if not pieces:
        return None
    return _SEP_CLASS.join(re.escape(p) for p in pieces)


def _candidates_for(name: str, aliases: list[str]) -> list[str]:
    """All match tokens for a project, longest raw token first.

    Longest-first ordering makes ``memU-experiment`` win over ``memU`` at a
    shared position (Python ``re`` alternation is leftmost-*first*, not
    leftmost-longest), so a more specific project name isn't shadowed by a
    shorter sibling.
    """
    seen: set[str] = set()
    tokens: list[str] = []
    for tok in [name, *aliases]:
        tok = (tok or "").strip()
        key = tok.lower()
        if not tok or key in seen:
            continue
        seen.add(key)
        tokens.append(tok)
    return sorted(tokens, key=len, reverse=True)


def detect_mentioned_projects(
    prompt: str,
    paths: Optional[Paths] = None,
    *,
    limit: int = 3,
) -> list[Project]:
    """Return the registered projects explicitly named in ``prompt``.

    Deterministic, case-insensitive, word-bounded match against canonical
    project names + aliases. Matches are non-overlapping and longest-first, so
    each span is attributed to its most specific project. Results preserve
    registry order and are capped at ``limit`` (bounds injection size when a
    prompt name-drops several projects). Empty/whitespace prompts short-circuit.

    Never raises on bad config or a malformed registry — memory recall must
    degrade gracefully, never break the prompt hook.
    """
    if not prompt or not prompt.strip():
        return []
    paths = paths or resolve()

    try:
        registry = _load_registry(paths)
    except Exception as exc:  # noqa: BLE001 - registry read must not break recall
        logger.warning("mention detection: could not load registry: %s", exc)
        return []
    central = _load_central_aliases(paths)

    # Collect every (token, registry-index) candidate, then order the combined
    # alternation by token length descending — GLOBALLY, across all projects.
    # Python ``re`` alternation is leftmost-*first*, so a shorter sibling token
    # ("memU") registered before a longer one ("memU-experiment") would
    # otherwise win at a shared position. Global longest-first fixes that.
    entries: list[dict] = []
    candidates: list[tuple[str, int]] = []  # (raw token, entry index)
    for entry in registry:
        name = str(entry.get("name") or "").strip()
        if not name:  # global sentinel / malformed row
            continue
        idx = len(entries)
        entries.append(entry)
        aliases = list(entry.get("aliases") or []) + central.get(name, [])
        for tok in _candidates_for(name, aliases):
            candidates.append((tok, idx))
    candidates.sort(key=lambda c: len(c[0]), reverse=True)

    group_to_idx: dict[str, int] = {}
    parts: list[str] = []
    for gid, (tok, idx) in enumerate(candidates):
        frag = _token_variant(tok)
        if frag is None:
            continue
        gname = f"m{gid}"
        group_to_idx[gname] = idx
        # ``(?<!\w)`` / ``(?!\w)`` word boundaries treat the separator chars as
        # boundaries (plain ``\b`` mishandles trailing dots).
        parts.append(f"(?<!\\w)(?P<{gname}>{frag})(?!\\w)")

    if not parts:
        return []

    try:
        pattern = re.compile("|".join(parts), re.IGNORECASE)
    except re.error as exc:  # pragma: no cover - defensive
        logger.warning("mention detection: bad pattern: %s", exc)
        return []

    seen_idx: set[int] = set()
    for m in pattern.finditer(prompt):
        grp = m.lastgroup
        if grp is None:
            continue
        idx = group_to_idx.get(grp)
        if idx is not None:
            seen_idx.add(idx)

    # Emit in registry order (stable, deterministic), then cap at ``limit`` so a
    # prompt name-dropping many projects can't blow the injection budget.
    out: list[Project] = []
    for idx in sorted(seen_idx)[:limit]:
        entry = entries[idx]
        name = str(entry.get("name"))
        proj = Project.for_name(name, paths=paths)
        if proj is None:
            proj = Project(name=name, path=str(entry.get("path") or ""))
        out.append(proj)
    return out
