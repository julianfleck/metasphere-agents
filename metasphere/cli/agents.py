"""CLI shims for the agent lifecycle module.

Command surface::

    metasphere-spawn @name /scope/ "task" [@parent]
    metasphere-wake  @name ["first task"]
    metasphere-wake  --list | --status
    agents list
    agents status
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
    "Usage:\n"
    "  metasphere-spawn @agent /scope/ \"task description\" [@parent]\n"
    "       [--authority \"...\"] [--responsibility \"...\"] [--accountability \"...\"]\n"
    "\n"
    "Contract fields (strongly recommended, treated as required in a\n"
    "future release — see agent-economy/NOTES-METASPHERE.md for rationale):\n"
    "  --authority       What the agent MAY do (scope of allowed actions)\n"
    "  --responsibility  What the agent MUST produce (artifact contract)\n"
    "  --accountability  How parent will verify on !done (concrete check)\n"
)


def _extract_flag(argv: list[str], flag: str) -> tuple[str, list[str]]:
    """Return (value, argv_without_flag). Accepts --flag=value or --flag value."""
    out: list[str] = []
    value = ""
    i = 0
    while i < len(argv):
        a = argv[i]
        if a == flag and i + 1 < len(argv):
            value = argv[i + 1]
            i += 2
            continue
        if a.startswith(flag + "="):
            value = a[len(flag) + 1 :]
            i += 1
            continue
        out.append(a)
        i += 1
    return value, out


def spawn_main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    # Peel off contract flags first so they can appear anywhere.
    authority, argv = _extract_flag(argv, "--authority")
    responsibility, argv = _extract_flag(argv, "--responsibility")
    accountability, argv = _extract_flag(argv, "--accountability")

    if len(argv) < 3:
        print(_SPAWN_USAGE, file=sys.stderr)
        return 1
    agent_id, scope_path, task = argv[0], argv[1], argv[2]
    parent = argv[3] if len(argv) >= 4 else "@orchestrator"

    # Nudge: warn loudly when spawning without a contract so the
    # operator (or orchestrator) feels the friction. Don't hard-block
    # yet — that breaks every legacy spawn site.
    if not (authority or responsibility or accountability):
        print(
            "warning: spawning without --authority/--responsibility/--accountability.\n"
            "         Legacy spawn accepted, but the contract-first form is strongly\n"
            "         preferred. See agent-economy/NOTES-METASPHERE.md.",
            file=sys.stderr,
        )

    rec = _agents.spawn_ephemeral(
        agent_id,
        scope_path,
        task,
        parent,
        authority=authority,
        responsibility=responsibility,
        accountability=accountability,
    )
    print(f"Spawned {rec.name}")
    print(f"  Scope:  {rec.scope}")
    print(f"  Parent: {rec.parent}")
    print(f"  Task:   {task}")
    if authority:
        print(f"  Authority:       {authority[:100]}")
    if responsibility:
        print(f"  Responsibility:  {responsibility[:100]}")
    if accountability:
        print(f"  Accountability:  {accountability[:100]}")
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
# seed from spec
# ---------------------------------------------------------------------------

def _list_specs() -> int:
    from metasphere import specs as _specs
    items = _specs.list_specs()
    if not items:
        print("No specs found. Place YAML files in specs/ or ~/.metasphere/specs/")
        return 0
    print("Available agent specs:")
    for s in items:
        print(f"  {s.name:16s} {s.role:16s} {s.description}")
    return 0


def _seed(argv: list[str]) -> int:
    """Seed an agent's persona files from a spec.

    Usage: metasphere agent seed --spec <spec-name> @agent-id [--project <name>]
    """
    from metasphere import specs as _specs

    spec_name = ""
    agent_id = ""
    project_name = ""
    force = False
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--spec" and i + 1 < len(argv):
            spec_name = argv[i + 1]
            i += 2
        elif arg == "--project" and i + 1 < len(argv):
            project_name = argv[i + 1]
            i += 2
        elif arg == "--force":
            force = True
            i += 1
        elif arg.startswith("@"):
            agent_id = arg
            i += 1
        else:
            i += 1

    if not spec_name or not agent_id:
        print(
            "Usage: metasphere agent seed --spec <spec-name> @agent-id [--project <name>] [--force]",
            file=sys.stderr,
        )
        return 1

    spec = _specs.get_spec(spec_name)
    if not spec:
        print(f"Spec '{spec_name}' not found.", file=sys.stderr)
        print("Available specs:")
        for s in _specs.list_specs():
            print(f"  {s.name}")
        return 1

    # Load project context if specified
    project_goal = ""
    project_scope = ""
    if project_name:
        from metasphere import project as _proj
        try:
            proj = _proj.load_project(project_name)
            project_goal = proj.goal or ""
            project_scope = proj.path
        except Exception:
            pass

    agent_dir = _specs.seed_agent(
        agent_id,
        spec,
        project_name=project_name,
        project_goal=project_goal,
        scope=project_scope or "",
        force=force,
    )
    print(f"Seeded {agent_id} from spec '{spec_name}'")
    print(f"  Directory: {agent_dir}")
    print(f"  Files: SOUL.md, MISSION.md, persona-index.md, LEARNINGS.md")
    print(f"  Wake with: metasphere agent wake {agent_id}")
    return 0


# ---------------------------------------------------------------------------
# `agents` umbrella entrypoint
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("--help", "-h"):
        print(__doc__ or "")
        return 0
    if not argv or argv[0] in ("list", "--list"):
        return _list()
    if argv[0] in ("status", "--status"):
        return _status()
    if argv[0] == "spawn":
        return spawn_main(argv[1:])
    if argv[0] == "wake":
        return wake_main(argv[1:])
    if argv[0] == "seed":
        return _seed(argv[1:])
    if argv[0] == "specs":
        return _list_specs()
    print("Usage: agents [list|status|spawn|wake|seed|specs]", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
