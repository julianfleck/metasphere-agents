"""CLI shims for the agent lifecycle module.

Command surface::

    metasphere agent spawn @name /scope/ "task" [@parent]
    metasphere agent wake  @name ["first task"]
    metasphere agent wake  --list | --status
    metasphere agent contract @name
    agents list
    agents status
"""

from __future__ import annotations

import sys
from pathlib import Path

from metasphere import agents as _agents
from metasphere import paths as _paths


def _list(project_filter: str | None = None) -> int:
    p = _paths.resolve()
    items = _agents.list_agents(p)
    persistent = [a for a in items if a.is_persistent]
    if project_filter:
        persistent = [a for a in persistent
                      if getattr(a, "project", None) == project_filter]
    if not persistent:
        print("No persistent agents.")
        return 0
    header = "Persistent agents (have MISSION.md):"
    if project_filter:
        header = f"Persistent agents [{project_filter}]:"
    print(header)
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
    "future release — see agent-economy/NOTES-DEEPMIND-DELEGATION.md for rationale):\n"
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
            "         preferred. See agent-economy/NOTES-DEEPMIND-DELEGATION.md.",
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
# contract entrypoint (formerly "verify")
# ---------------------------------------------------------------------------

_CONTRACT_USAGE = (
    "Usage:\n"
    "  metasphere agent contract @name\n"
    "\n"
    "Print the delegation contract for a spawned agent so the parent\n"
    "can re-read authority/responsibility/accountability before\n"
    "accepting a !done message.\n"
    "\n"
    "Looks in:\n"
    "  1. Live agent dir: ~/.metasphere/agents/@name/{authority,responsibility,accountability}\n"
    "  2. GC'd agent log: ~/.metasphere/logs/agents/*/@name.log\n"
)


def _read_sidecar(agent_dir: Path, name: str) -> str:
    f = agent_dir / name
    if f.is_file():
        try:
            return f.read_text(encoding="utf-8").strip()
        except (OSError, UnicodeDecodeError):
            pass
    return ""


def _find_gc_log(paths: _paths.Paths, agent_name: str) -> Path | None:
    """Find the GC preservation log for an agent that was already cleaned up."""
    logs_dir = paths.logs / "agents"
    if not logs_dir.is_dir():
        return None
    for project_dir in sorted(logs_dir.iterdir()):
        if not project_dir.is_dir():
            continue
        log = project_dir / f"{agent_name}.log"
        if log.is_file():
            return log
    return None


def _parse_contract_from_log(log_path: Path) -> dict[str, str]:
    """Extract contract fields from a GC'd agent's preserved log.

    The log has sections delimited by ``--- <filename> ---`` lines.
    We look for the authority, responsibility, and accountability
    sections (from the sidecar-preserve path added in e3d6100+).

    Fallback: if sidecar fields are absent (agent was GC'd before that
    fix), parse the Delegation Contract block from the harness.md
    section, which always contained the rendered contract.
    """
    text = log_path.read_text(encoding="utf-8")
    result: dict[str, str] = {}
    current_section = ""
    section_bodies: dict[str, str] = {}
    lines: list[str] = []
    KEEP = ("authority", "responsibility", "accountability",
            "task", "status", "parent", "spawned_at", "harness.md")
    for line in text.splitlines():
        if line.startswith("--- ") and line.endswith(" ---"):
            if current_section in KEEP:
                section_bodies[current_section] = "\n".join(lines).strip()
            section_name = line[4:-4].strip()
            current_section = section_name
            lines = []
        else:
            lines.append(line)
    if current_section in KEEP:
        section_bodies[current_section] = "\n".join(lines).strip()

    # Direct sidecar fields (post-e3d6100 GC)
    for key in ("authority", "responsibility", "accountability",
                "task", "status", "parent", "spawned_at"):
        if key in section_bodies:
            result[key] = section_bodies[key]

    # Fallback: parse from harness.md if sidecar fields not found
    if not result.get("authority") and "harness.md" in section_bodies:
        harness = section_bodies["harness.md"]
        result.update(_parse_contract_from_harness(harness))

    return result


def _parse_contract_from_harness(harness_text: str) -> dict[str, str]:
    """Extract authority/responsibility/accountability from a rendered
    Delegation Contract block in a harness.md file.
    """
    result: dict[str, str] = {}
    mapping = {
        "### Authority (what you MAY do)": "authority",
        "### Responsibility (what you MUST produce)": "responsibility",
        "### Accountability (how parent will verify)": "accountability",
    }
    current_key = ""
    lines: list[str] = []
    for line in harness_text.splitlines():
        if line in mapping:
            if current_key:
                result[current_key] = "\n".join(lines).strip()
            current_key = mapping[line]
            lines = []
        elif line.startswith("### ") or line.startswith("## ") or line == "---":
            if current_key:
                result[current_key] = "\n".join(lines).strip()
                current_key = ""
                lines = []
        elif current_key:
            lines.append(line)
    if current_key:
        result[current_key] = "\n".join(lines).strip()
    return result


def contract_main(argv: list[str] | None = None) -> int:
    """Print the delegation contract for a spawned agent."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(_CONTRACT_USAGE, file=sys.stderr)
        return 1
    agent_name = argv[0]
    if not agent_name.startswith("@"):
        agent_name = f"@{agent_name}"

    paths = _paths.resolve()
    agent_dir = paths.agents / agent_name

    if agent_dir.is_dir():
        # Live agent — read sidecar files directly
        authority = _read_sidecar(agent_dir, "authority")
        responsibility = _read_sidecar(agent_dir, "responsibility")
        accountability = _read_sidecar(agent_dir, "accountability")
        task = _read_sidecar(agent_dir, "task")
        status = _read_sidecar(agent_dir, "status")
        parent = _read_sidecar(agent_dir, "parent")
        spawned_at = _read_sidecar(agent_dir, "spawned_at")
        source = f"(live agent dir: {agent_dir})"
    else:
        # GC'd agent — try the log
        log_path = _find_gc_log(paths, agent_name)
        if log_path is None:
            print(f"No agent dir or GC log found for {agent_name}.", file=sys.stderr)
            return 1
        fields = _parse_contract_from_log(log_path)
        authority = fields.get("authority", "")
        responsibility = fields.get("responsibility", "")
        accountability = fields.get("accountability", "")
        task = fields.get("task", "")
        status = fields.get("status", "")
        parent = fields.get("parent", "")
        spawned_at = fields.get("spawned_at", "")
        source = f"(from GC log: {log_path})"

    has_contract = bool(authority or responsibility or accountability)

    print(f"DELEGATION CONTRACT for {agent_name}")
    print(f"  {source}")
    print()
    if spawned_at:
        print(f"  Spawned:  {spawned_at}")
    if parent:
        print(f"  Parent:   {parent}")
    if task:
        print(f"  Task:     {task}")
    if status:
        print(f"  Status:   {status}")
    print()

    if not has_contract:
        print("  (no contract — legacy spawn without authority/responsibility/accountability)")
        return 0

    print("AUTHORITY (what they MAY do):")
    print(f"  {authority or '(unspecified)'}")
    print()
    print("RESPONSIBILITY (what they MUST produce):")
    print(f"  {responsibility or '(unspecified)'}")
    print()
    print("ACCOUNTABILITY (how to verify on !done):")
    print(f"  {accountability or '(unspecified)'}")
    print()

    # Check for deliverables directory
    logs_dir = paths.logs / "agents"
    if logs_dir.is_dir():
        for project_dir in logs_dir.iterdir():
            deliv_dir = project_dir / agent_name
            if deliv_dir.is_dir():
                deliverables = list(deliv_dir.glob("*.md"))
                if deliverables:
                    print("PRESERVED DELIVERABLES:")
                    for d in sorted(deliverables):
                        print(f"  {d}")
                    print()

    return 0


# ---------------------------------------------------------------------------
# wake entrypoint
# ---------------------------------------------------------------------------

_WAKE_USAGE = (
    "Usage:\n"
    "  metasphere agent wake @agent [\"first task\"]\n"
    "  metasphere agent wake --list\n"
    "  metasphere agent wake --status\n"
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
        print(f"metasphere agent wake: {e}", file=sys.stderr)
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
        project_arg = argv[1] if len(argv) > 1 and not argv[1].startswith("-") else None
        return _list(project_filter=project_arg)
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
