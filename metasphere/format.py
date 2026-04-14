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
INDENT = "       "  # 7 spaces; metadata visually offset under the title
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


AGENT_STATUS_EMOJI = {
    "active": "🟢",
    "idle": "⚪",
    "spawned": "🔵",
    "working": "🟡",
    "waiting": "🟠",
    "complete": "🟢",
}


def _agent_status_emoji(status: str) -> str:
    s = (status or "").strip().lower()
    for prefix, emoji in AGENT_STATUS_EMOJI.items():
        if s.startswith(prefix):
            return emoji
    return "🔵"


def _agent_card(agent, *, html: bool, live: bool = False) -> str:
    name = agent.name or "unknown"
    status = agent.status or "unknown"
    project = getattr(agent, "project", "") or ""
    live_mark = " [live]" if live else ""

    lines = [f"{_agent_status_emoji(status)}  {_b(_esc(name + live_mark, html), html)}"]
    # Show just the status text (e.g. "active: persistent session")
    lines.append(f"{INDENT}{_esc(ellipsize(status, 60), html)}")
    if project:
        lines.append(f"{INDENT}Project: {_esc(project, html)}")
    return "\n".join(lines)


def format_task_table(
    tasks: Sequence,
    *,
    html: bool | None = None,
    agents: Sequence | None = None,
) -> str:
    """Render tasks as mobile-first cards, optionally with active agents.

    ``html``: if ``None`` (default), inferred from ``METASPHERE_HTML`` env
    var; the telegram dispatch path sets that so the captured stdout of the
    CLI carries HTML markup. CLI users get plain text.

    ``agents``: optional list of ``(agent_record, is_live)`` tuples. If
    provided, live agents are shown as an "Active Agents" section after
    the tasks.
    """
    html = _resolve_html(html)
    header = _b("Tasks", html)
    if not tasks and not agents:
        return f"{header}\n{RULE}\n(no tasks)"
    parts = [header, RULE]
    for t in tasks:
        parts.append(_task_card(t, html=html))
        parts.append(RULE)
    if not tasks:
        parts.append("(no task files)")
        parts.append(RULE)

    # Active agents section
    if agents:
        live_agents = [(a, live) for a, live in agents if live]
        if live_agents:
            parts.append("")
            parts.append(_b(f"Active Agents ({len(live_agents)})", html))
            parts.append(RULE)
            for a, live in live_agents:
                parts.append(_agent_card(a, html=html, live=True))
                parts.append(RULE)

    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Condensed (one-line-per-task) task view
# ---------------------------------------------------------------------------

#: Target width for the per-project header rule.
CONDENSED_HEADER_WIDTH = 42
#: Max title width in the condensed one-liner.
CONDENSED_TITLE_MAX = 70


def _condensed_priority_label(priority: str) -> str:
    """Return a fixed-width priority tag for column alignment.

    ``!high`` / ``!normal`` / ``!low`` render with trailing padding so that
    titles line up in a column. Unknown/missing priorities render as blanks
    of the same width so the title column still aligns.
    """
    p = (priority or "").strip()
    # Widest is !normal (7 chars); pad to that.
    width = 7
    if p in ("!high", "!normal", "!low"):
        return p.ljust(width)
    return " " * width


def _condensed_task_line(task, *, html: bool) -> str:
    emoji = task_status_emoji(task.status, assignee=getattr(task, "assignee", ""))
    prio = _condensed_priority_label(task.priority or "!normal")
    title = ellipsize((task.title or "").strip(), CONDENSED_TITLE_MAX)
    return f"{emoji} {_esc(prio, html)} {_esc(title, html)}"


def _condensed_project_header(name: str, count: int, *, html: bool) -> str:
    label = f" {name} ({count}) "
    remaining = max(CONDENSED_HEADER_WIDTH - len(label) - 2, 4)
    left = 2
    right = remaining - 0
    head = "─" * left + label + "─" * right
    return _b(head, html)


def format_task_condensed(
    tasks: Sequence,
    *,
    html: bool | None = None,
    group_by_project: bool = True,
) -> str:
    """Render tasks as one line per task, grouped under per-project headers.

    Designed for the all-projects "give me everything active" view — both
    the ``metasphere task list`` fallback (no project context, no filter)
    and the Telegram ``/tasks`` bare command. Priority of *what this shows*
    is total scannable density: one glance tells the user which projects
    have active work and roughly what it is.

    Each task renders as::

        {status-emoji} {priority-padded} {title-truncated}

    with titles truncated to ``CONDENSED_TITLE_MAX`` characters.

    Ordering within a project: high-priority first, then normal, then low;
    within a priority bucket, pending before in-progress before everything
    else, then alphabetical on title for stability. Projects themselves
    are sorted alphabetically.
    """
    html = _resolve_html(html)
    header = _b("Tasks", html)
    if not tasks:
        return f"{header}\n(no active tasks)"

    # Group
    buckets: dict[str, list] = {}
    for t in tasks:
        key = (getattr(t, "project", None) or "default") if group_by_project else "tasks"
        buckets.setdefault(key, []).append(t)

    # Sort keys + tasks
    priority_order = {"!high": 0, "!normal": 1, "!low": 2}
    status_order = {"in-progress": 0, "in_progress": 0, "pending": 1, "blocked": 2}

    def _sort_key(task):
        return (
            priority_order.get(task.priority or "!normal", 1),
            status_order.get(task.status or "pending", 3),
            (task.title or "").lower(),
        )

    parts: list[str] = [header]
    for proj_name in sorted(buckets.keys()):
        items = sorted(buckets[proj_name], key=_sort_key)
        parts.append("")
        parts.append(_condensed_project_header(proj_name, len(items), html=html))
        for t in items:
            parts.append(_condensed_task_line(t, html=html))
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


# ---------------------------------------------------------------------------
# Project cards
# ---------------------------------------------------------------------------

PROJECT_STATUS_EMOJI = {
    "active": "🟢",
    "archived": "⚪",
    "missing": "🔴",
}


def _project_card(proj, *, html: bool) -> str:
    status = getattr(proj, "status", "") or "active"
    emoji = PROJECT_STATUS_EMOJI.get(status, "🔵")
    name = ellipsize(proj.name or "", TITLE_MAX)
    goal = getattr(proj, "goal", None) or ""
    members = getattr(proj, "members", None) or []
    path = getattr(proj, "path", "") or ""

    lines = [f"{emoji}  {_b(_esc(name, html), html)}"]
    lines.append(f"{INDENT}Status: {_esc(status, html)}")
    if members:
        member_ids = ", ".join(getattr(m, "id", str(m)) for m in members[:5])
        if len(members) > 5:
            member_ids += f" (+{len(members) - 5})"
        lines.append(f"{INDENT}Members: {_esc(member_ids, html)}")
    if goal:
        lines.append(f"{INDENT}Goal: {_esc(ellipsize(goal, 60), html)}")
    if path:
        lines.append(f"{INDENT}Path: {_esc(path, html)}")
    return "\n".join(lines)


def format_project_table(projects: Sequence, *, html: bool | None = None) -> str:
    """Render a list of projects as mobile-first cards."""
    html = _resolve_html(html)
    header = _b("Projects", html)
    if not projects:
        return f"{header}\n{RULE}\n(no projects)"

    # Separate initialized from uninitialized
    active = [p for p in projects if getattr(p, "status", "") != "missing"]
    missing = [p for p in projects if getattr(p, "status", "") == "missing"]

    parts = [header, RULE]
    for p in active:
        parts.append(_project_card(p, html=html))
        parts.append(RULE)

    if missing:
        parts.append(f"({len(missing)} registered but not initialized)")

    return "\n".join(parts)
