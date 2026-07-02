"""System status summary — single-screen liveness snapshot.

Backs ``metasphere status``: composes a short human-readable report of
the currently alive tmux sessions and basic install layout. Reads
only — pulls live session state from ``metasphere.session.list_sessions``
and path layout from ``metasphere.paths.resolve``, no on-disk writes.
Designed to be cheap enough to invoke on every ``watch`` tick and from
the top of operator-facing briefings.
"""

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
        mark = "●" if s.attached else "○"
        lines.append(f"  {mark} {s.agent}")

    # Tasks — aggregate OPEN (non-terminal) tasks across every registered
    # project, with a per-state breakdown so "open" never silently hides a
    # blocked or paused task again. Previously this counted a single scope
    # with a 3-string allowlist, so widget's paused queue and metasphere's
    # blocked tasks read as zero (the "0 active" symptom).
    try:
        from collections import Counter
        from .tasks import active_tasks_across_projects

        active = active_tasks_across_projects(paths)
        if active:
            def _norm(s: str) -> str:
                return "in-progress" if s == "in_progress" else s

            by_state = Counter(_norm(t.status) for t in active)
            nproj = len({t.project for t in active if t.project})
            order = ("pending", "in-progress", "blocked", "paused")
            parts = [f"{by_state[s]} {s}" for s in order if by_state.get(s)]
            parts += [f"{n} {s}" for s, n in sorted(by_state.items())
                      if s not in order]
            suffix = f" across {nproj} project{'s' if nproj != 1 else ''}"
            lines.append(
                f"\nTasks: {len(active)} open ({', '.join(parts)}){suffix}"
            )
        else:
            lines.append("\nTasks: 0 open")
    except Exception as exc:
        lines.append(f"\nTasks: (unavailable: {type(exc).__name__}: {exc})")

    # Schedule
    try:
        from .schedule import list_jobs

        jobs = list_jobs(paths)
        enabled = [j for j in jobs if getattr(j, "enabled", True)]
        lines.append(f"Schedule: {len(enabled)} jobs enabled")
    except Exception as exc:
        lines.append(f"Schedule: (unavailable: {type(exc).__name__}: {exc})")

    # Projects
    try:
        from .project import list_projects

        projects = list_projects(paths=paths)
        initialized = [p for p in projects if p.status != "missing"]
        lines.append(f"Projects: {len(initialized)} initialized")
    except Exception as exc:
        lines.append(f"Projects: (unavailable: {type(exc).__name__}: {exc})")

    # Gateway
    try:
        from .gateway.session import session_health

        alive, idle = session_health(paths)
        if alive:
            lines.append(f"\nOrchestrator: alive (idle {idle}s)")
        else:
            lines.append("\nOrchestrator: not running")
    except Exception as exc:
        lines.append(f"\nOrchestrator: (status unavailable: {type(exc).__name__}: {exc})")

    # Daemons (systemd user services). A dead heartbeat or schedule
    # daemon is a silent failure mode otherwise — the REPL keeps
    # looking healthy while no ticks fire.
    try:
        from .cli.restart import daemon_health

        health = daemon_health()
        lines.append("\nDaemons:")
        for name, active in health.items():
            mark = "●" if active else "○"
            state = "active" if active else "inactive"
            lines.append(f"  {mark} {name}: {state}")
    except Exception as exc:
        lines.append(f"\nDaemons: (unavailable: {type(exc).__name__}: {exc})")

    return "\n".join(lines)
