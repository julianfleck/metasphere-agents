"""System status summary."""

from __future__ import annotations

from .paths import resolve
from .session import list_sessions


def summary() -> str:
    """Return a human-readable system status overview."""
    paths = resolve()
    lines = []

    # Session status
    sessions = list_sessions()
    alive_count = len(sessions)
    lines.append(f"Sessions: {alive_count} active")
    for s in sessions:
        mark = "*" if s.attached else " "
        lines.append(f"  {mark} {s.agent}")

    # Tasks
    try:
        from .tasks import list_tasks

        tasks = list_tasks(paths.repo)
        active = [t for t in tasks if t.status in ("pending", "in-progress", "in_progress")]
        lines.append(f"\nTasks: {len(active)} active")
    except Exception:
        lines.append("\nTasks: (unavailable)")

    # Schedule
    try:
        from .schedule import list_jobs

        jobs = list_jobs(paths)
        enabled = [j for j in jobs if getattr(j, "enabled", True)]
        lines.append(f"Schedule: {len(enabled)} jobs enabled")
    except Exception:
        lines.append("Schedule: (unavailable)")

    # Projects
    try:
        from .project import list_projects

        projects = list_projects(paths=paths)
        initialized = [p for p in projects if p.status != "missing"]
        lines.append(f"Projects: {len(initialized)} initialized")
    except Exception:
        lines.append("Projects: (unavailable)")

    # Gateway
    try:
        from .gateway.session import session_health

        alive, idle = session_health(paths)
        if alive:
            lines.append(f"\nOrchestrator: alive (idle {idle}s)")
        else:
            lines.append("\nOrchestrator: not running")
    except Exception:
        lines.append("\nOrchestrator: (status unavailable)")

    return "\n".join(lines)
