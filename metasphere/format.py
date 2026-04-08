"""Shared ASCII table formatter for CLI + Telegram output.

Used by ``tasks list`` and ``schedule list``. Produces a compact, plain
ASCII table that renders legibly in mobile Telegram (no markdown, no
Unicode box drawing, just pipes and dashes), with colored-circle emojis
for status.

Color rules:
  * ANSI escape sequences are emitted ONLY when stdout is a tty AND
    ``NO_COLOR`` / ``METASPHERE_PLAIN`` are both unset. Telegram paths
    force ``METASPHERE_PLAIN=1`` so they always land in the plain branch.
  * Emoji status bubbles are rendered unconditionally — Telegram
    natively displays them as colored characters, which is the intended
    source of color in forwarded output.

Timestamps are formatted as ``YYYY-MM-DD HH:MM`` in UTC, no seconds, no
timezone marker.
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
from dataclasses import dataclass
from typing import Iterable, Sequence

# ---------------------------------------------------------------------------
# Status → emoji
# ---------------------------------------------------------------------------

TASK_STATUS_EMOJI = {
    "pending": "🔵",
    "in-progress": "🟡",
    "in_progress": "🟡",
    "blocked": "🔴",
    "completed": "🟢",
    "stale": "🟣",
    "unowned": "⚪",
}

SCHED_STATUS_EMOJI = {
    "enabled": "🟢",
    "disabled": "🔴",
}


def task_status_emoji(status: str, *, assignee: str = "") -> str:
    s = (status or "").strip().lower()
    if s in TASK_STATUS_EMOJI:
        return TASK_STATUS_EMOJI[s]
    # unknown status → treat as muted/unowned bubble
    if not assignee:
        return "⚪"
    return "🔵"


def sched_status_emoji(enabled: bool) -> str:
    return SCHED_STATUS_EMOJI["enabled"] if enabled else SCHED_STATUS_EMOJI["disabled"]


# ---------------------------------------------------------------------------
# Plain-mode detection
# ---------------------------------------------------------------------------


def is_plain_mode() -> bool:
    if os.environ.get("METASPHERE_PLAIN"):
        return True
    if os.environ.get("NO_COLOR"):
        return True
    if not sys.stdout.isatty():
        return True
    return False


# ---------------------------------------------------------------------------
# Timestamp helpers
# ---------------------------------------------------------------------------


def fmt_iso_ts(raw: str) -> str:
    """Parse a Z-suffixed ISO timestamp and format as 'YYYY-MM-DD HH:MM' UTC.

    Returns ``'-'`` for empty or unparseable inputs.
    """
    if not raw:
        return "-"
    s = raw.strip()
    if not s:
        return "-"
    try:
        if s.endswith("Z"):
            s2 = s[:-1] + "+00:00"
        else:
            s2 = s
        dt = _dt.datetime.fromisoformat(s2)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=_dt.timezone.utc)
        return dt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
    except Exception:
        # already human-formatted or YYYY-MM-DD prefix
        return s[:16] if len(s) >= 10 else s


def fmt_epoch_ts(ts: int) -> str:
    if not ts:
        return "-"
    try:
        return _dt.datetime.fromtimestamp(int(ts), _dt.timezone.utc).strftime(
            "%Y-%m-%d %H:%M"
        )
    except Exception:
        return "-"


# ---------------------------------------------------------------------------
# String truncation
# ---------------------------------------------------------------------------


def ellipsize(s: str, width: int) -> str:
    if width <= 0:
        return ""
    if len(s) <= width:
        return s
    if width <= 1:
        return s[:width]
    return s[: width - 1] + "…"


# ---------------------------------------------------------------------------
# Table rendering
# ---------------------------------------------------------------------------


@dataclass
class Column:
    header: str
    width: int
    align: str = "left"  # 'left' or 'right'


def _pad(cell: str, col: Column) -> str:
    # Emoji glyphs are typically double-width in terminals but count as 1
    # code point in Python len(). We do NOT try to correct for this — the
    # table is intended for monospace-ish rendering in a chat, where the
    # first-column emoji variance is acceptable.
    text = ellipsize(cell, col.width)
    if col.align == "right":
        return text.rjust(col.width)
    return text.ljust(col.width)


def render_table(columns: Sequence[Column], rows: Iterable[Sequence[str]]) -> str:
    """Render a simple ASCII pipe-separated table with a header rule.

    Layout::

        HDR1    | HDR2       | HDR3
        --------+------------+-----
        cell    | cell       | cell

    No unicode box drawing — mobile telegram rendering is unreliable.
    """
    header = " | ".join(_pad(c.header, c) for c in columns)
    rule = "-+-".join("-" * c.width for c in columns)
    lines = [header, rule]
    for row in rows:
        padded = [_pad(str(cell), col) for cell, col in zip(row, columns)]
        lines.append(" | ".join(padded))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Task-list rendering
# ---------------------------------------------------------------------------


TASK_COLUMNS = (
    Column("S", 2),          # status emoji (2 chars because some emojis render wide)
    Column("PRI", 7),        # !normal / !high / !urgent / !low
    Column("TITLE", 24),     # truncated title
    Column("ID", 22),        # task id (ellipsized to fit)
    Column("OWNER", 14),     # @agent
    Column("PROJECT", 10),
    Column("CREATED", 16),   # YYYY-MM-DD HH:MM
    Column("UPDATED", 16),
)


def format_task_row(task) -> list[str]:
    """Turn a ``metasphere.tasks.Task`` into a list of cell strings."""
    emoji = task_status_emoji(task.status, assignee=task.assignee)
    owner = task.assignee or "@unassigned"
    project = (getattr(task, "project", None) or "default")
    return [
        emoji,
        task.priority or "!normal",
        task.title or "",
        task.id or "",
        owner,
        project,
        fmt_iso_ts(task.created),
        fmt_iso_ts(task.updated),
    ]


def format_task_table(tasks: Sequence) -> str:
    rows = [format_task_row(t) for t in tasks]
    return render_table(TASK_COLUMNS, rows)


# ---------------------------------------------------------------------------
# Schedule-list rendering
# ---------------------------------------------------------------------------


SCHED_COLUMNS = (
    Column("S", 2),
    Column("NAME", 22),
    Column("ID", 18),
    Column("AGENT", 14),
    Column("EXPR", 16),
    Column("LAST FIRED", 16),
    Column("NEXT", 16),
)


def _next_fire_for_cron(expr: str, tz: str) -> str:
    """Best-effort next-fire computation. Returns '-' if unavailable."""
    if not expr:
        return "-"
    try:
        from croniter import croniter  # type: ignore
        try:
            from zoneinfo import ZoneInfo
            zone = ZoneInfo(tz or "UTC")
        except Exception:
            zone = _dt.timezone.utc
        now = _dt.datetime.now(zone)
        nxt = croniter(expr, now).get_next(_dt.datetime)
        return nxt.astimezone(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return "-"


def format_job_row(job) -> list[str]:
    emoji = sched_status_emoji(bool(getattr(job, "enabled", True)))
    return [
        emoji,
        job.name or "",
        job.id or "",
        f"@{job.agent_id}" if job.agent_id and not str(job.agent_id).startswith("@")
        else str(job.agent_id or ""),
        job.cron_expr or job.kind or "",
        fmt_epoch_ts(getattr(job, "last_fired_at", 0)),
        _next_fire_for_cron(job.cron_expr or "", getattr(job, "tz", "UTC") or "UTC"),
    ]


def format_schedule_table(jobs: Sequence) -> str:
    rows = [format_job_row(j) for j in jobs]
    return render_table(SCHED_COLUMNS, rows)
