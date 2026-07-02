"""``metasphere agent`` — agent lifecycle CLI shim.

Dispatches the ``agent`` subcommand family (``list``, ``status``,
``spawn``, ``wake``, ``seed``, ``specs``) onto ``metasphere.agents`` and
``metasphere.specs``. Handles both persistent agents (long-lived tmux +
REPL) and ephemeral one-shots; the ``spawn`` path also threads
Authority/Responsibility/Accountability fields into the agent's
bootstrap so every fan-out carries A/R/A context.
"""

from __future__ import annotations

DESCRIPTION = "Spawn, wake, list, and seed metasphere agents."

USAGE = """\
Usage: metasphere agent {list|status|spawn|wake|seed|specs} [args...]

Commands:
  list [project]    List persistent agents (optionally filtered by
                    project).
  status            Show liveness + idle for each persistent agent.
  spawn @name /scope/ "task description" [@parent]
                    [--authority "..."]
                    [--responsibility "..."]
                    [--accountability "..."]
                    Spawn an ephemeral one-shot agent. Authority /
                    Responsibility / Accountability fields are strongly
                    recommended (treated as required in a future
                    release).
  wake @name ["first task"] [--model MODEL]
                    Wake a dormant persistent agent (re-attaches its
                    tmux session and injects an optional first task).
                    `--model` overrides the model for the woken
                    session (e.g. claude-opus-4-8).
  seed --spec <spec> @agent-id [--project <name>] [--force]
                    Materialize the per-role `AGENTS.md` into the
                    agent's home. The seeder looks up
                    `templates/agents/<spec.role>/AGENTS.md`
                    (e.g. spec `reviewer` → role `critic`).
                    Without `--force`, the seeder refuses to
                    overwrite an existing per-agent `AGENTS.md`.
                    Use `--force` to re-seed an existing agent
                    after the shipped template changes (e.g. after
                    a harness update modifies the runtime nudges
                    in `templates/agents/<role>/AGENTS.md`). Live
                    persistent agents do not pick up template
                    updates automatically — the operator runs this
                    per-agent.
  specs             List available agent type specs.

Each subcommand has its own --help where additional flags exist.
"""


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

    # Group: global (project == "") first, then project-scoped buckets
    # alphabetically. Within each bucket sort by name.
    buckets: dict[str, list] = {}
    for a in persistent:
        buckets.setdefault(getattr(a, "project", "") or "", []).append(a)
    for v in buckets.values():
        v.sort(key=lambda a: a.name)
    bucket_order = []
    if "" in buckets:
        bucket_order.append("")
    bucket_order.extend(sorted(k for k in buckets if k))

    for project in bucket_order:
        label = project if project else "global"
        print(f"  {label}/")
        for a in buckets[project]:
            marker = "●" if _agents.session_alive(a.session_name) else "○"
            print(f"    {marker} {a.name}")
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
    "  metasphere agent spawn @agent /scope/ \"task description\" [@parent]\n"
    "       [--authority \"...\"] [--responsibility \"...\"] [--accountability \"...\"]\n"
    "\n"
    "Contract fields (strongly recommended, treated as required in a\n"
    "future release):\n"
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
    if argv and argv[0] in ("--help", "-h"):
        sys.stdout.write(_SPAWN_USAGE)
        return 0
    # Peel off contract flags first so they can appear anywhere.
    authority, argv = _extract_flag(argv, "--authority")
    responsibility, argv = _extract_flag(argv, "--responsibility")
    accountability, argv = _extract_flag(argv, "--accountability")

    if len(argv) < 3:
        print(_SPAWN_USAGE, file=sys.stderr)
        return 1
    agent_id, scope_path, task = argv[0], argv[1], argv[2]
    parent = argv[3] if len(argv) >= 4 else "@orchestrator"

    # Pre-check the agent name before the contract-warning nudge so a
    # flag-shaped typo (`metasphere agent spawn --bogus ...`) doesn't
    # surface a confusing "Legacy spawn accepted" line before the real
    # rejection. spawn_ephemeral re-validates as defense in depth.
    try:
        _agents._validate_agent_name(agent_id)
    except ValueError as e:
        print(f"metasphere agent spawn: {e}", file=sys.stderr)
        return 2

    # Spawn was excluded from 27dccc4's trailing-arg sweep. Without
    # these guards, `metasphere agent spawn @x / "task" --bogus`
    # silently took ``--bogus`` as the parent and proceeded; any 5th+
    # positional was dropped with rc=0. Both are typo classes the
    # operator wants surfaced, not absorbed.
    if len(argv) >= 4 and argv[3].startswith("-"):
        print(
            f"metasphere agent spawn: parent looks like a CLI flag, not an @agent: {argv[3]}\n"
            f"(contract flags must precede positional args: "
            f"--authority/--responsibility/--accountability)",
            file=sys.stderr,
        )
        return 2
    if len(argv) > 4:
        extra = argv[4]
        kind = "flag" if extra.startswith("-") else "argument"
        print(
            f"metasphere agent spawn: unexpected trailing {kind}: {extra}\n"
            f"Usage: metasphere agent spawn @agent /scope/ \"task\" [@parent]",
            file=sys.stderr,
        )
        return 2

    # Nudge: warn loudly when spawning without a contract so the
    # operator (or orchestrator) feels the friction. Don't hard-block
    # yet — that breaks every legacy spawn site.
    if not (authority or responsibility or accountability):
        print(
            "warning: spawning without --authority/--responsibility/--accountability.\n"
            "         Legacy spawn accepted, but the contract-first form is strongly\n"
            "         preferred.",
            file=sys.stderr,
        )

    try:
        rec = _agents.spawn_ephemeral(
            agent_id,
            scope_path,
            task,
            parent,
            authority=authority,
            responsibility=responsibility,
            accountability=accountability,
        )
    except ValueError as e:
        print(f"metasphere agent spawn: {e}", file=sys.stderr)
        return 2
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
    "  metasphere agent wake @agent [\"first task\"] [--model MODEL]\n"
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
    rest = argv[1:]
    model = ""
    if "--model" in rest:
        idx = rest.index("--model")
        if idx + 1 >= len(rest):
            print(
                "metasphere agent wake: --model requires a value (e.g. claude-opus-4-7)",
                file=sys.stderr,
            )
            return 1
        model = rest[idx + 1]
        rest = rest[:idx] + rest[idx + 2 :]
    first_task = rest[0] if rest else None
    if first_task and first_task.startswith("-"):
        print(
            f"metasphere agent wake: '{first_task}' looks like a flag, not a task.\n"
            f"Usage: metasphere agent wake @agent [\"first task\"] [--model MODEL]",
            file=sys.stderr,
        )
        return 1
    if len(rest) > 1:
        # Trailing args after agent + optional first_task are almost
        # always a typo'd flag (`wake @x "task" --bogus`). The pre-
        # hardening path silently dropped them and reported success.
        extra = rest[1]
        kind = "flag" if extra.startswith("-") else "argument"
        print(
            f"metasphere agent wake: unexpected trailing {kind}: {extra}\n"
            f"Usage: metasphere agent wake @agent [\"first task\"] [--model MODEL]",
            file=sys.stderr,
        )
        return 2
    try:
        rec, delivered = _agents.wake_persistent(
            agent, first_task=first_task, model=model
        )
    except ValueError as e:
        print(f"metasphere agent wake: {e}", file=sys.stderr)
        return 1
    print(f"{rec.name} awake. Attach with: tmux attach -t {rec.session_name}")
    if first_task and not delivered:
        print(
            "WARNING: first-task inject did not land on the pane "
            "(silent tmux submit failure). Re-run wake or send via "
            "`metasphere msg send`.",
            file=sys.stderr,
        )
        return 1
    return 0


# ---------------------------------------------------------------------------
# seed from spec
# ---------------------------------------------------------------------------

def _list_specs() -> int:
    from metasphere import specs as _specs
    items = _specs.list_specs()
    if not items:
        print(
            "No specs found. Create a role directory under "
            "~/.metasphere/templates/agents/<role>/ (or "
            "templates/agents/<role>/ in a repo checkout) with at "
            "least config.md — see templates/agents/README.md."
        )
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

    try:
        agent_dir = _specs.seed_agent(
            agent_id,
            spec,
            project_name=project_name,
            project_goal=project_goal,
            scope=project_scope or "",
            force=force,
        )
    except ValueError as e:
        print(f"metasphere agent seed: {e}", file=sys.stderr)
        return 2
    print(f"Seeded {agent_id} from spec '{spec_name}'")
    print(f"  Directory: {agent_dir}")
    print(f"  Files: SOUL.md, MISSION.md, persona-index.md, LEARNINGS.md")
    print(f"  Wake with: metasphere agent wake {agent_id}")
    return 0


# ---------------------------------------------------------------------------
# `agents` umbrella entrypoint
# ---------------------------------------------------------------------------

def _reject_extra(subcmd: str, extras: list[str]) -> int:
    """Emit a stderr complaint about leftover args and return rc=2.

    Used by read-side leaf commands (``list``, ``status``, ``specs``)
    that have a fixed argv shape. Without this, an unknown flag like
    ``metasphere agent list --filter=foo`` silently succeeded with the
    full unfiltered output — the typo never surfaced.
    """
    head = extras[0]
    if head.startswith("-"):
        print(f"metasphere agent {subcmd}: unknown flag: {head}", file=sys.stderr)
    else:
        print(f"metasphere agent {subcmd}: unexpected argument: {head}", file=sys.stderr)
    return 2


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("--help", "-h"):
        sys.stdout.write(USAGE)
        return 0
    if not argv or argv[0] in ("list", "--list"):
        rest = argv[1:] if argv and argv[0] in ("list", "--list") else argv
        project_arg = None
        if rest:
            if rest[0].startswith("-"):
                return _reject_extra("list", rest)
            project_arg = rest[0]
            rest = rest[1:]
        if rest:
            return _reject_extra("list", rest)
        return _list(project_filter=project_arg)
    if argv[0] in ("status", "--status"):
        if argv[1:]:
            return _reject_extra("status", argv[1:])
        return _status()
    if argv[0] == "spawn":
        return spawn_main(argv[1:])
    if argv[0] == "wake":
        return wake_main(argv[1:])
    if argv[0] == "seed":
        return _seed(argv[1:])
    if argv[0] == "specs":
        if argv[1:]:
            return _reject_extra("specs", argv[1:])
        return _list_specs()
    print(f"metasphere agent: unknown subcommand {argv[0]!r}", file=sys.stderr)
    sys.stderr.write(USAGE)
    return 2


if __name__ == "__main__":
    sys.exit(main())
