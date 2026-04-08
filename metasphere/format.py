"""Shared card formatter for CLI + Telegram output.

Used by ``metasphere task list`` and ``metasphere schedule list``. Produces
a mobile-first card layout: each item is rendered as a multi-line card with
an emoji status bubble, the title, and indented metadata lines, separated by
an em-dash rule. Telegram chat width on a phone is too narrow for the old
ASCII pipe-table; cards wrap naturally and stay readable.

When ``html=True`` is passed, titles and key metadata are wrapped in
``<b>...</b>`` so the Telegram bot can deliver the message with
``parse_mode='HTML'``. The CLI path leaves ``html=False`` so terminals
print plain text.

Color rules:
  * Emoji status bubbles render as colored characters in Telegram and most
    terminals; that is the only source of color in this module.
  * No ANSI escapes — the table renderer that used them was removed along
    with the table itself.

Timestamps are formatted as ``YYYY-MM-DD HH:MM`` in UTC, no seconds.
"""

from __future__ import annotations

import datetime as _dt
import os
import sys
from typing import Sequence

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
    if not assignee:
        return "⚪"
    return "🔵"


def sched_status_emoji(enabled: bool) -> str:
    return SCHED_STATUS_EMOJI["enabled"] if enabled else SCHED_STATUS_EMOJI["disabled"]


# ---------------------------------------------------------------------------
# Plain-mode detection (kept for back-compat with telegram dispatch path)
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
    """Parse a Z-suffixed ISO timestamp and format as 'YYYY-MM-DD HH:MM' UTC."""
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
# HTML helpers
# ---------------------------------------------------------------------------


def escape_html(s: str) -> str:
    """Escape ``& < >`` for Telegram parse_mode='HTML'.

    Telegram's HTML parser only requires escaping these three characters in
    text content (and inside <b>/<i>/<a>). It does NOT require escaping
    quotes. We deliberately leave quotes alone so they read naturally.
    """
    if not s:
        return ""
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _b(text: str, html: bool) -> str:
    """Bold ``text`` if rendering for HTML, otherwise plain (text already escaped)."""
    return f"<b>{text}</b>" if html else text


def _esc(text: str, html: bool) -> str:
    return escape_html(text) if html else text


# ---------------------------------------------------------------------------
# Card layout
# ---------------------------------------------------------------------------

#: Em-dash rule between cards. Matches the user's mockup (~25 dashes wide).
RULE = "—" * 25
INDENT = "      "  # 6 spaces; metadata visually offset under the title
TITLE_MAX = 40


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


def _task_card(task, *, html: bool) -> str:
    emoji = task_status_emoji(task.status, assignee=task.assignee)
    title = ellipsize(task.title or "", TITLE_MAX)
    owner = task.assignee or "@unassigned"
    project = getattr(task, "project", None) or "default"
    priority = task.priority or "!normal"
    status = task.status or "pending"
    created = fmt_iso_ts(task.created)
    updated = fmt_iso_ts(task.updated)

    lines = [f"{emoji}  {_b(_esc(title, html), html)}"]
    lines.append(f"{INDENT}Created: {_esc(created, html)}")
    lines.append(f"{INDENT}Owner: {_b(_esc(owner, html), html)}")
    lines.append(f"{INDENT}Project: {_b(_esc(project, html), html)}")
    lines.append(f"{INDENT}Priority: {_esc(priority, html)}")
    lines.append(f"{INDENT}Status: {_esc(status, html)}")
    if updated and updated != "-" and updated != created:
        lines.append(f"{INDENT}Updated: {_esc(updated, html)}")
    return "\n".join(lines)


def _resolve_html(html):
    if html is None:
        return bool(os.environ.get("METASPHERE_HTML"))
    return bool(html)


def format_task_table(tasks: Sequence, *, html: bool | None = None) -> str:
    """Render a list of tasks as mobile-first cards.

    Name kept (not ``format_task_cards``) so existing call sites don't churn.
    The shape it returns is a card stack, not a table.

    ``html``: if ``None`` (default), inferred from ``METASPHERE_HTML`` env
    var; the telegram dispatch path sets that so the captured stdout of the
    CLI carries HTML markup. CLI users get plain text.
    """
    html = _resolve_html(html)
    header = _b("Tasks", html)
    if not tasks:
        return f"{header}\n{RULE}\n(no tasks)"
    parts = [header, RULE]
    for t in tasks:
        parts.append(_task_card(t, html=html))
        parts.append(RULE)
    return "\n".join(parts)


def _job_card(job, *, html: bool) -> str:
    emoji = sched_status_emoji(bool(getattr(job, "enabled", True)))
    name = ellipsize(job.name or "", TITLE_MAX)
    agent_id = job.agent_id or ""
    if agent_id and not str(agent_id).startswith("@"):
        agent_id = f"@{agent_id}"
    expr = job.cron_expr or job.kind or ""
    last_fired = fmt_epoch_ts(getattr(job, "last_fired_at", 0))
    next_fire = _next_fire_for_cron(job.cron_expr or "", getattr(job, "tz", "UTC") or "UTC")
    last_status = getattr(job, "last_status", "") or "-"

    lines = [f"{emoji}  {_b(_esc(name, html), html)}"]
    lines.append(f"{INDENT}Agent: {_b(_esc(str(agent_id), html), html)}")
    lines.append(f"{INDENT}Expression: {_esc(expr, html)}")
    lines.append(f"{INDENT}Last fired: {_esc(last_fired, html)}")
    lines.append(f"{INDENT}Next fire: {_esc(next_fire, html)}")
    lines.append(f"{INDENT}Last status: {_esc(str(last_status), html)}")
    return "\n".join(lines)


def format_schedule_table(jobs: Sequence, *, html: bool | None = None) -> str:
    """Render a list of cron jobs as mobile-first cards."""
    html = _resolve_html(html)
    header = _b("Schedule", html)
    if not jobs:
        return f"{header}\n{RULE}\n(no jobs)"
    parts = [header, RULE]
    for j in jobs:
        parts.append(_job_card(j, html=html))
        parts.append(RULE)
    return "\n".join(parts)
