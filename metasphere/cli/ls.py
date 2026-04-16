"""``metasphere ls`` — project / task / agent landscape.

Pure-Python port of the legacy ``cmd_ls`` function from the deleted
``scripts/metasphere`` bash script. Two modes::

    metasphere ls            # top-level landscape (projects, events,
                             # agents grouped by status, tasks, msgs)
    metasphere ls @agent     # deep dive on a single agent

Output is deliberately terse and line-oriented so it works the same in
an interactive TTY and when piped to a log. TTY-only ANSI colour is
used when ``sys.stdout.isatty()``; everything else stays plain.

The bash version also shelled out to ``jq`` for the events timestamp
re-formatting and to the old ``scripts/tasks`` bin for a task table.
Both are now delegated to the canonical Python modules
(:mod:`metasphere.events` + :mod:`metasphere.tasks`) so we render from
the same source of truth the rest of the CLI uses.
"""

from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from metasphere import agents as _agents
from metasphere import events as _events
from metasphere import paths as _paths
from metasphere import project as _project
from metasphere import session as _session
from metasphere.paths import Paths


# ---------------------------------------------------------------------------
# Terminal helpers (TTY-only colour, silent pipeable output otherwise)
# ---------------------------------------------------------------------------

def _tty() -> bool:
    return sys.stdout.isatty()


class _C:
    """ANSI colour codes, or empty strings when not a TTY."""

    def __init__(self, enabled: bool) -> None:
        if enabled:
            self.red = "\033[0;31m"
            self.yellow = "\033[1;33m"
            self.green = "\033[0;32m"
            self.cyan = "\033[0;36m"
            self.dim = "\033[2m"
            self.bold = "\033[1m"
            self.nc = "\033[0m"
        else:
            self.red = self.yellow = self.green = self.cyan = ""
            self.dim = self.bold = self.nc = ""


def _ok(c: _C, text: str) -> str:
    return f"  {c.green}●{c.nc} {text}"


def _warn(c: _C, text: str) -> str:
    return f"  {c.yellow}○{c.nc} {text}"


def _dim(c: _C, text: str) -> str:
    return f"  {c.dim}{text}{c.nc}"


# ---------------------------------------------------------------------------
# Time display
# ---------------------------------------------------------------------------

def _time_display(paths: Paths) -> str:
    """Return ``HH:MM`` honoring an optional user timezone file.

    Mirrors the bash logic: if ``$METASPHERE_DIR/config/timezone`` exists
    and is a valid IANA zone name, render local time with the zone name
    suffix; otherwise fall back to UTC.
    """
    tz_file = paths.config / "timezone"
    user_tz: Optional[str] = None
    if tz_file.is_file():
        try:
            user_tz = tz_file.read_text(encoding="utf-8").strip()
        except OSError:
            user_tz = None
    if user_tz:
        try:
            # Python 3.9+ zoneinfo — available on all metasphere targets.
            from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
            try:
                now = datetime.now(ZoneInfo(user_tz))
                return f"{now:%H:%M} ({user_tz})"
            except ZoneInfoNotFoundError:
                pass
        except ImportError:
            pass
    return f"{datetime.now(timezone.utc):%H:%M}"


# ---------------------------------------------------------------------------
# Sub-renders
# ---------------------------------------------------------------------------

def _render_session(c: _C, lines: list[str]) -> None:
    """Orchestrator-tmux-session health line."""
    if _session.session_alive is None:  # pragma: no cover — defensive
        return
    # The orchestrator session is named ``metasphere-orchestrator``;
    # use the canonical helper so any future rename flows through.
    alive = _agents.session_alive("metasphere-orchestrator")
    if alive:
        lines.append(f"  {c.green}●{c.nc} session active")
    else:
        lines.append(f"  {c.yellow}○{c.nc} no session")


def _count_active_tasks(project_path: Path) -> int:
    td = project_path / ".tasks" / "active"
    if not td.is_dir():
        return 0
    try:
        return sum(1 for _ in td.glob("*.md"))
    except OSError:
        return 0


def _count_agents_for_scope(paths: Paths, project_path: Path) -> int:
    """Count agents whose `scope` file is rooted under ``project_path``."""
    if not paths.agents.is_dir():
        return 0
    count = 0
    for d in paths.agents.iterdir():
        if not d.is_dir() or not d.name.startswith("@"):
            continue
        scope_file = d / "scope"
        try:
            scope_val = scope_file.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if scope_val and scope_val.startswith(str(project_path)):
            count += 1
    return count


def _render_projects(c: _C, paths: Paths, lines: list[str],
                     *, project_filter: str | None = None) -> None:
    lines.append(f"{c.bold}Projects{c.nc}")
    try:
        projects = _project.list_projects(paths=paths)
    except Exception:
        projects = []
    if not projects:
        lines.append(_dim(c, "(no projects - run: metasphere project init)"))
        return
    if project_filter:
        projects = [p for p in projects if p.name == project_filter]
        if not projects:
            lines.append(_dim(c, f"(no project named '{project_filter}')"))
            return
    for p in projects:
        pp = Path(p.path) if p.path else None
        # ``list_projects`` marks a project ``missing`` when its project.json
        # can't be loaded from either the repo dir or
        # ``~/.metasphere/projects/<name>/``. That is the authoritative
        # "missing" signal — the bash version's ``-d $path/.metasphere``
        # check was a rough approximation and reported false positives for
        # projects whose on-disk home moved to ``~/.metasphere/projects/``.
        if p.status == "missing" or pp is None or not pp.exists():
            lines.append(f"  {c.red}○{c.nc} {p.name} {c.dim}(missing){c.nc}")
            continue
        tasks = _count_active_tasks(pp)
        agents = _count_agents_for_scope(paths, pp)
        lines.append(
            f"  {c.green}●{c.nc} {p.name} "
            f"{c.dim}({tasks} tasks, {agents} agents){c.nc}"
        )


def _render_events(c: _C, paths: Paths, lines: list[str]) -> None:
    lines.append(f"{c.bold}Events{c.nc}")
    try:
        tail = _events.tail_events(3, paths=paths)
    except Exception:
        tail = "(no events)"
    if tail == "(no events)" or not tail.strip():
        lines.append(_dim(c, "(none)"))
        return
    for ev_line in tail.splitlines():
        # tail_events already formats ``HH:MM:SSZ [type] @agent: message``.
        lines.append(f"  {ev_line}")


def _render_agents(c: _C, paths: Paths, lines: list[str],
                   *, project_filter: str | None = None) -> None:
    lines.append(f"{c.bold}Agents{c.nc}")
    active: list[str] = []
    spawned: list[str] = []
    other: list[str] = []
    try:
        records = _agents.list_agents(paths)
    except Exception:
        records = []
    if project_filter:
        records = [r for r in records
                   if getattr(r, "project", None) == project_filter]
    for rec in records:
        status = rec.status or "unknown"
        head = status.split(":", 1)[0]
        if head in ("active", "working"):
            active.append(rec.name)
        elif head == "spawned":
            spawned.append(rec.name)
        else:
            other.append(rec.name)

    if active:
        lines.append(f"  {c.green}●{c.nc} active: {' '.join(active)}")
    if spawned:
        lines.append(f"  {c.yellow}◐{c.nc} spawned: {' '.join(spawned)}")
    if other:
        lines.append(f"  {c.dim}○{c.nc} other: {' '.join(other)}")
    if not (active or spawned or other):
        lines.append(_dim(c, "(none)"))


def _render_tasks(c: _C, paths: Paths, lines: list[str]) -> None:
    """Task summary across the project root and all registered projects.

    The bash version invoked the legacy ``scripts/tasks`` binary and
    piped to ``head -10``; we use :func:`metasphere.tasks.list_tasks`
    against the configured scope+repo instead.
    """
    lines.append(f"{c.bold}Tasks{c.nc}")
    try:
        from metasphere.tasks import list_tasks

        tasks = list_tasks(paths.scope, paths.project_root)
    except Exception:
        lines.append(_dim(c, "(no tasks)"))
        return
    active = [t for t in tasks if getattr(t, "status", "") in
              ("pending", "in-progress", "in_progress", "active", "")]
    if not active:
        lines.append(_dim(c, "(no active tasks)"))
        return
    # Render up to 10 task lines; tasks expose ``.id`` + ``.title`` +
    # optional priority.
    for t in active[:10]:
        tid = getattr(t, "id", "?")
        title = getattr(t, "title", "") or ""
        prio = getattr(t, "priority", "") or ""
        prio_fmt = f" !{prio}" if prio and prio != "normal" else ""
        lines.append(f"  {tid}{prio_fmt} {title[:60]}")
    if len(active) > 10:
        lines.append(_dim(c, f"... +{len(active) - 10} more"))


def _count_pending_messages(paths: Paths) -> int:
    """Count ``.messages/inbox/*.msg`` files under ~/.metasphere/*."""
    total = 0
    # Global root
    roots = [paths.root, paths.project_root]
    for root in roots:
        if not root.is_dir():
            continue
        try:
            for p in root.rglob(".messages/inbox/*.msg"):
                if p.is_file():
                    total += 1
        except OSError:
            continue
    return total


def _render_messages(c: _C, paths: Paths, lines: list[str]) -> None:
    total = _count_pending_messages(paths)
    if total > 0:
        lines.append(f"{c.bold}Messages{c.nc}: {total} pending")


# ---------------------------------------------------------------------------
# Per-agent view (``metasphere ls @name``)
# ---------------------------------------------------------------------------

def _render_agent(c: _C, paths: Paths, agent: str, lines: list[str]) -> int:
    agent_dir = paths.agents / agent
    if not agent_dir.is_dir():
        sys.stderr.write(f"Agent not found: {agent}\n")
        return 1
    lines.append(f"{c.bold}{agent}{c.nc}")
    lines.append("")

    def _read(name: str, default: str = "") -> str:
        fp = agent_dir / name
        try:
            return fp.read_text(encoding="utf-8").strip()
        except OSError:
            return default

    status = _read("status") or "unknown"
    lines.append(f"Status: {status}")

    task = _read("task")
    if task:
        lines.append("")
        lines.append(f"{c.bold}Task{c.nc}")
        lines.append(task)

    scope = _read("scope")
    if scope:
        home = str(Path.home())
        if scope.startswith(home):
            scope = "~" + scope[len(home):]
        lines.append("")
        lines.append(f"Scope: {scope}")

    sandbox = _read("sandbox")
    if sandbox:
        lines.append(f"Sandbox: {sandbox}")

    children_file = agent_dir / "children"
    if children_file.is_file() and children_file.stat().st_size > 0:
        lines.append("")
        lines.append(f"{c.bold}Children{c.nc}")
        try:
            for child in children_file.read_text(encoding="utf-8").splitlines():
                child = child.strip()
                if not child:
                    continue
                cstatus = _read_file(paths.agents / child / "status") or "?"
                lines.append(f"  {child}: {cstatus}")
        except OSError:
            pass

    lines.append("")
    lines.append(f"{c.bold}Recent Events{c.nc}")
    agent_events = _agent_event_tail(paths, agent, 5)
    if not agent_events:
        lines.append("(none)")
    else:
        for ev in agent_events:
            lines.append(ev)

    if agent == "@orchestrator":
        lines.append("")
        lines.append(f"{c.bold}Session{c.nc}")
        if _agents.session_alive("metasphere-orchestrator"):
            lines.append("Active (attach: metasphere-gateway attach)")
        else:
            lines.append("Not running")

    return 0


def _read_file(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _agent_event_tail(paths: Paths, agent: str, n: int) -> list[str]:
    """Walk the dated event logs newest-first, keeping the last *n*
    entries whose ``agent`` field matches ``agent``.

    Returns a list of formatted ``HH:MM:SS [type] message`` lines ready
    to print.
    """
    events_dir = paths.events
    dated = []
    if events_dir.is_dir():
        dated = sorted(events_dir.glob("events-*.jsonl"))
    files = list(reversed(dated)) if dated else [events_dir / "events.jsonl"]
    matches: list[str] = []
    for log in files:
        if not log.is_file():
            continue
        try:
            raws = log.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        # Walk bottom-up so we pick up the freshest matches first.
        for raw in reversed(raws):
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if rec.get("agent") != agent:
                continue
            ts = rec.get("timestamp", "")
            time_part = (
                ts.split("T", 1)[1].split(".")[0] if "T" in ts else ts
            )
            typ = rec.get("type", "")
            msg = (rec.get("message", "") or "").replace("\n", " ")[:40]
            matches.append(f"{time_part} {typ}: {msg}")
            if len(matches) >= n:
                break
        if len(matches) >= n:
            break
    # We collected newest-first; flip so oldest-of-tail is printed first.
    return list(reversed(matches))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str]) -> int:
    if argv and argv[0] in ("-h", "--help"):
        sys.stdout.write(
            "Usage: metasphere ls [@agent | project-name]\n"
            "  (no arg)      Show top-level landscape\n"
            "  @agent        Show detail view for that agent\n"
            "  project-name  Show landscape filtered to one project\n"
        )
        return 0

    paths = _paths.resolve()
    c = _C(_tty())
    lines: list[str] = []

    # Agent-specific view
    if argv and argv[0].startswith("@"):
        rc = _render_agent(c, paths, argv[0], lines)
        sys.stdout.write("\n".join(lines) + "\n")
        return rc

    project_filter = argv[0] if argv and not argv[0].startswith("-") else None

    # Top-level landscape
    lines.append(f"{c.bold}Metasphere{c.nc} {_time_display(paths)}")
    _render_session(c, lines)
    lines.append("")
    _render_projects(c, paths, lines, project_filter=project_filter)
    lines.append("")
    _render_events(c, paths, lines)
    lines.append("")
    _render_agents(c, paths, lines, project_filter=project_filter)
    lines.append("")
    _render_tasks(c, paths, lines)
    _render_messages(c, paths, lines)

    sys.stdout.write("\n".join(lines) + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
