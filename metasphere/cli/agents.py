"""CLI shims for the agent lifecycle module.

Mirrors the bash command surface::

    metasphere-spawn @name /scope/ "task" [@parent]
    metasphere-wake  @name ["first task"]
    metasphere-wake  --list | --status
    agents list
    agents status

The bash entrypoints (``scripts/metasphere-spawn``, ``scripts/metasphere-wake``)
are slated to become thin shims around these.
"""

from __future__ import annotations

import sys

from metasphere import agents as _agents
from metasphere import paths as _paths


def _list() -> int:
    p = _paths.resolve()
    items = _agents.list_agents(p)
    persistent = [a for a in items if a.is_persistent]
    if not persistent:
        print("No persistent agents.")
        return 0
    print("Persistent agents (have MISSION.md):")
    for a in persistent:
        marker = "●" if _agents.session_alive(a.session_name) else "○"
        print(f"  {marker} {a.name}")
    return 0


def _status() -> int:
    p = _paths.resolve()
    items = _agents.list_agents(p)
    persistent = [a for a in items if a.is_persistent]
    if not persistent:
        print("No persistent agents.")
        return 0
    print("Persistent agent sessions:")
    for a in persistent:
        if _agents.session_alive(a.session_name):
            idle = _agents._session_idle_seconds(a.session_name)
            idle_s = f"idle {idle}s" if idle is not None else "idle ?"
            print(f"  ● {a.name} (session: {a.session_name}, {idle_s})")
        else:
            print(f"  ○ {a.name} (dormant)")
    return 0


# ---------------------------------------------------------------------------
# spawn entrypoint
# ---------------------------------------------------------------------------

_SPAWN_USAGE = (
    "Usage: metasphere-spawn @agent /scope/ \"task description\" [@parent]"
)


def spawn_main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if len(argv) < 3:
        print(_SPAWN_USAGE, file=sys.stderr)
        return 1
    agent_id, scope_path, task = argv[0], argv[1], argv[2]
    parent = argv[3] if len(argv) >= 4 else "@orchestrator"
    rec = _agents.spawn_ephemeral(agent_id, scope_path, task, parent)
    print(f"Spawned {rec.name}")
    print(f"  Scope:  {rec.scope}")
    print(f"  Parent: {rec.parent}")
    print(f"  Task:   {task}")
    if rec.pid_file and rec.pid_file.is_file():
        print(f"  PID:    {rec.pid_file.read_text().strip()}")
    return 0


# ---------------------------------------------------------------------------
# wake entrypoint
# ---------------------------------------------------------------------------

_WAKE_USAGE = (
    "Usage:\n"
    "  metasphere-wake @agent [\"first task\"]\n"
    "  metasphere-wake --list\n"
    "  metasphere-wake --status\n"
)


def wake_main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print(_WAKE_USAGE, file=sys.stderr)
        return 1
    head = argv[0]
    if head in ("--list", "list"):
        return _list()
    if head in ("--status", "status"):
        return _status()
    if head.startswith("-"):
        print(f"Unknown flag: {head}", file=sys.stderr)
        return 1
    agent = head
    first_task = argv[1] if len(argv) >= 2 else None
    try:
        rec = _agents.wake_persistent(agent, first_task=first_task)
    except ValueError as e:
        print(f"metasphere-wake: {e}", file=sys.stderr)
        return 1
    print(f"{rec.name} awake. Attach with: tmux attach -t {rec.session_name}")
    return 0


# ---------------------------------------------------------------------------
# `agents` umbrella entrypoint
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("list", "--list"):
        return _list()
    if argv[0] in ("status", "--status"):
        return _status()
    if argv[0] == "spawn":
        return spawn_main(argv[1:])
    if argv[0] == "wake":
        return wake_main(argv[1:])
    print("Usage: agents [list|status|spawn ...|wake ...]", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
