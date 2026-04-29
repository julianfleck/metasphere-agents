"""Broadcast directives — parsed from DIRECTIVES.yaml at project root.

Directives are standing orders from the human or orchestrator that all
agents in a project should obey. They propagate to running agents at
the next heartbeat tick via the context injection system, without
requiring a session restart.

Format: sequence of ``---``-delimited blocks. Each block has ``key: value``
lines. The ``text`` field runs to the next ``---`` or EOF. Pure stdlib
parser — no PyYAML dependency.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .paths import Paths

DIRECTIVES_FILENAME = "DIRECTIVES.yaml"
DEFAULT_MAX_N = 10


@dataclass
class Directive:
    date: str = ""       # "2026-04-12"
    source: str = ""     # "@user"
    text: str = ""       # the directive body
    expires: str = ""    # "" means no expiry


def parse_directives(content: str) -> list[Directive]:
    """Parse a DIRECTIVES.yaml string into a list of Directive objects."""
    if not content or not content.strip():
        return []

    # Split on document separator. Handle leading --- at file start.
    blocks = content.split("\n---")
    result: list[Directive] = []

    for block in blocks:
        block = block.strip()
        if not block:
            continue

        d = Directive()
        in_text = False
        text_lines: list[str] = []

        for line in block.splitlines():
            if in_text:
                text_lines.append(line)
                continue

            stripped = line.strip()
            if stripped.startswith("date:"):
                d.date = stripped[len("date:"):].strip()
            elif stripped.startswith("source:"):
                d.source = stripped[len("source:"):].strip()
            elif stripped.startswith("expires:"):
                d.expires = stripped[len("expires:"):].strip()
            elif stripped.startswith("text:"):
                rest = stripped[len("text:"):].strip()
                if rest:
                    text_lines.append(rest)
                in_text = True

        d.text = "\n".join(text_lines).strip()
        # Only include if there's actual content
        if d.text or d.date:
            result.append(d)

    return result


def _is_expired(d: Directive, today: str = "") -> bool:
    """True if the directive has an expiry date in the past."""
    if not d.expires:
        return False
    if not today:
        today = _dt.date.today().isoformat()
    return d.expires < today


def load_directives(
    paths: "Paths",
    *,
    max_n: int = DEFAULT_MAX_N,
    today: str = "",
) -> list[Directive]:
    """Load active directives from the project root."""
    fpath = paths.project_root / DIRECTIVES_FILENAME
    if not fpath.is_file():
        return []
    try:
        content = fpath.read_text(encoding="utf-8")
    except OSError:
        return []

    all_dirs = parse_directives(content)
    active = [d for d in all_dirs if not _is_expired(d, today=today)]
    return active[-max_n:]


def add_directive(
    paths: "Paths",
    text: str,
    source: str = "",
    expires: str = "",
) -> Directive:
    """Append a new directive to DIRECTIVES.yaml."""
    if not source:
        try:
            from .identity import resolve_agent_id
            source = resolve_agent_id(paths)
        except Exception:
            source = "@orchestrator"

    d = Directive(
        date=_dt.date.today().isoformat(),
        source=source,
        text=text.strip(),
        expires=expires,
    )

    fpath = paths.project_root / DIRECTIVES_FILENAME
    block_lines = [
        "---",
        f"date: {d.date}",
        f"source: {d.source}",
    ]
    if d.expires:
        block_lines.append(f"expires: {d.expires}")
    block_lines.append(f"text: {d.text}")
    block_lines.append("")  # trailing newline

    content = "\n".join(block_lines)

    # Append to file (create if needed)
    fpath.parent.mkdir(parents=True, exist_ok=True)
    with open(fpath, "a", encoding="utf-8") as f:
        f.write(content)

    return d


def render_directives(paths: "Paths", max_n: int = DEFAULT_MAX_N) -> str:
    """Render directives as a markdown section for context injection."""
    items = load_directives(paths, max_n=max_n)
    if not items:
        return ""

    lines = ["## Directives (broadcast)", ""]
    for d in items:
        expiry = f" (expires {d.expires})" if d.expires else ""
        # Compact single-line format for context efficiency
        text_oneline = d.text.replace("\n", " ").strip()
        if len(text_oneline) > 200:
            text_oneline = text_oneline[:197] + "..."
        lines.append(f"- [{d.date}] {d.source}{expiry}: {text_oneline}")

    lines.append("")
    return "\n".join(lines)
