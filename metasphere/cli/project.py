"""``metasphere project`` — project + member-agent lifecycle.

Front-end for ``metasphere.project`` covering project creation, member
listing, and Telegram chat/topic wiring. Projects are the scoping unit
above individual agents: a project owns a directory under the
projects/ tree and a set of agent assignments. The shim parses argv
and dispatches; all state writes (project dirs, member rosters,
Telegram topic registration) go through the project module.
"""

from __future__ import annotations

DESCRIPTION = "Create, list, and manage projects + their member agents."

USAGE = """\
Usage: metasphere project <command> [args...]

Commands:
  new <name> [--path P] [--goal "..."] [--repo URL]
      [--member @x:role[:persistent]] ...
                                Create a new project with optional
                                metadata and member agents.
  init [path]                   Legacy minimal constructor: write
                                project metadata into [path].
  list                          List all registered projects.
  show [name]                   Print metadata for one project.
  rename <old-name> <new-name>  Rename a project (directory + metadata).

Member subcommands:
  member add <name> @agent [--role R] [--persistent]
                                Add an agent to the project's member
                                list.
  member remove <name> @agent   Remove an agent.
  member list [name]            List members.
  members [name]                Alias for `member list`.

Other:
  wake [name]                   Wake every persistent member of the
                                project.
  for [path]                    Print the enclosing project name for a
                                given path (or the current dir).
  chat <name> "message"         Post a message to the project's
                                Telegram topic.
  changelog [name]              Show the project changelog.
  learnings [name]              Show the project LEARNINGS file.
"""


import argparse
import sys
from pathlib import Path

from metasphere.paths import resolve


def _reject_flag_shape(value: str, op: str) -> int | None:
    """Return rc + print error/USAGE if ``value`` looks like a leaked CLI flag.

    Differs from the shared ``cli._argv.reject_flag_shape`` in that the
    project subcommands accept ``--help``/``-h`` inline at the
    positional slot (the subcommand parser hasn't yet intercepted them
    when this is called). Returns ``0`` after printing USAGE in that
    case.
    """
    if not value.startswith("-"):
        return None
    if value in ("--help", "-h"):
        sys.stdout.write(USAGE)
        return 0
    from metasphere.cli._argv import reject_flag_shape

    return reject_flag_shape(
        value, op, command="project", what="project name"
    )


def _parse_member_spec(spec: str) -> dict:
    """Parse ``@agent:role[:persistent]`` into a dict."""
    if not spec.startswith("@"):
        spec = "@" + spec
    parts = spec.split(":")
    out = {"id": parts[0], "role": "contributor", "persistent": False}
    if len(parts) >= 2 and parts[1]:
        out["role"] = parts[1]
    if len(parts) >= 3 and parts[2]:
        out["persistent"] = parts[2].lower() in ("1", "true", "persistent", "yes", "y")
    return out


def _cmd_new(rest: list[str], paths) -> int:
    from metasphere.project import new_project

    ap = argparse.ArgumentParser(prog="metasphere project new")
    ap.add_argument("name")
    ap.add_argument("--path", type=Path, default=None)
    ap.add_argument("--goal", default=None)
    ap.add_argument("--repo", default=None)
    ap.add_argument("--member", action="append", default=[],
                    help="@agent:role[:persistent], repeatable")
    ns = ap.parse_args(rest)
    members = [_parse_member_spec(m) for m in ns.member]
    try:
        proj = new_project(
            ns.name, path=ns.path, goal=ns.goal, repo=ns.repo,
            members=members, paths=paths,
        )
    except (FileExistsError, ValueError) as e:
        print(str(e), file=sys.stderr)
        return 2
    print(f"Created project: {proj.name}")
    print(f"  Path: {proj.path}")
    if proj.goal:
        print(f"  Goal: {proj.goal}")
    if proj.members:
        print(f"  Members: {', '.join(m.id for m in proj.members)}")
    if proj.telegram_topic:
        print(f"  Telegram topic: {proj.telegram_topic['name']} "
              f"(id={proj.telegram_topic['id']})")
    return 0


def _cmd_init(rest: list[str], paths) -> int:
    from metasphere.project import init_project
    ap = argparse.ArgumentParser(prog="metasphere project init")
    ap.add_argument("path", nargs="?", type=Path, default=None,
                    help="Project directory to initialize (default: cwd).")
    ns = ap.parse_args(rest)
    target = ns.path if ns.path is not None else Path.cwd()
    try:
        p = init_project(path=target, paths=paths)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    print(f"Initialized project: {p.name}")
    print(f"  Path: {p.path}")
    return 0


def _cmd_list(rest: list[str], paths) -> int:
    if rest:
        head = rest[0]
        kind = "flag" if head.startswith("-") else "argument"
        sys.stderr.write(
            f"metasphere project list: unexpected {kind}: {head}\n"
            f"Usage: metasphere project list (takes no arguments)\n"
        )
        return 2
    from metasphere.project import list_projects
    rows = list_projects(paths=paths)
    if not rows:
        print("(no projects)")
        return 0

    # Separate initialized from registered-only
    initialized = [p for p in rows if p.status != "missing"]
    registered = [p for p in rows if p.status == "missing"]

    if initialized:
        name_w = max(len(p.name) for p in initialized)
        for p in initialized:
            goal = ""
            if hasattr(p, "goal") and p.goal:
                goal = f"  {p.goal[:60]}"
            members = ""
            if hasattr(p, "members") and p.members:
                members = f"  [{len(p.members)} members]"
            print(f"  {p.name:<{name_w}}  {p.status:<10}{members}{goal}")

    if registered:
        if initialized:
            print()
        print(f"  ({len(registered)} registered but not initialized — "
              f"run `metasphere project init <path>` to set up)")
        if "--all" in rest or "-a" in rest:
            for p in registered:
                print(f"    {p.name}  {p.path}")

    return 0


def _cmd_show(rest: list[str], paths) -> int:
    from metasphere.project import get_project, project_for_scope
    if rest:
        rc = _reject_flag_shape(rest[0], "show")
        if rc is not None:
            return rc
    name = rest[0] if rest else None
    proj = get_project(name, paths=paths) if name else project_for_scope(Path.cwd(), paths=paths)
    if proj is None:
        print("project not found", file=sys.stderr)
        return 1
    print(f"Project: {proj.name}")
    print(f"  Path:    {proj.path}")
    print(f"  Status:  {proj.status}")
    print(f"  Schema:  {proj.schema}")
    if proj.goal:
        print(f"  Goal:    {proj.goal}")
    if proj.repo:
        print(f"  Repo:    {proj.repo.get('url')}")
    if proj.members:
        print("  Members:")
        for m in proj.members:
            tag = " (persistent)" if m.persistent else ""
            print(f"    - {m.id} [{m.role}]{tag}")
    else:
        print("  Members: (none)")
    if proj.telegram_topic:
        print(f"  Telegram topic: {proj.telegram_topic.get('name')} "
              f"(id={proj.telegram_topic.get('id')})")
    if proj.links:
        print(f"  Links:   {proj.links}")
    return 0


def _cmd_member(rest: list[str], paths) -> int:
    from metasphere.project import add_member, remove_member, list_members
    if not rest:
        print("usage: project member {add|remove|list} ...", file=sys.stderr)
        return 2
    verb, *args = rest
    if verb == "add":
        ap = argparse.ArgumentParser(prog="project member add")
        ap.add_argument("name")
        ap.add_argument("agent")
        ap.add_argument("--role", default="contributor")
        ap.add_argument("--persistent", action="store_true")
        ns = ap.parse_args(args)
        try:
            proj = add_member(ns.name, ns.agent, role=ns.role,
                              persistent=ns.persistent, paths=paths)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        print(f"Added {ns.agent} to {proj.name}")
        return 0
    if verb == "remove":
        ap = argparse.ArgumentParser(prog="project member remove")
        ap.add_argument("name")
        ap.add_argument("agent")
        ns = ap.parse_args(args)
        try:
            proj = remove_member(ns.name, ns.agent, paths=paths)
        except ValueError as e:
            print(str(e), file=sys.stderr)
            return 2
        print(f"Removed {ns.agent} from {proj.name}")
        return 0
    if verb in ("list", "ls"):
        if args:
            rc = _reject_flag_shape(args[0], "member list")
            if rc is not None:
                return rc
        name = args[0] if args else None
        from metasphere.project import project_for_scope
        if name is None:
            proj = project_for_scope(Path.cwd(), paths=paths)
            if proj is None:
                print("no project in scope", file=sys.stderr)
                return 1
            name = proj.name
        for m in list_members(name, paths=paths):
            tag = " (persistent)" if m.persistent else ""
            print(f"{m.id}\t{m.role}{tag}")
        return 0
    print(f"unknown member verb: {verb}", file=sys.stderr)
    return 2


def _cmd_wake(rest: list[str], paths) -> int:
    from metasphere.project import wake_members, project_for_scope
    if rest:
        rc = _reject_flag_shape(rest[0], "wake")
        if rc is not None:
            return rc
    name = rest[0] if rest else None
    if name is None:
        proj = project_for_scope(Path.cwd(), paths=paths)
        if proj is None:
            print("no project in scope", file=sys.stderr)
            return 1
        name = proj.name
    waked = wake_members(name, paths=paths)
    if not waked:
        print("(no persistent members to wake)")
    else:
        for a in waked:
            print(f"woken: {a}")
    return 0


def _cmd_for(rest: list[str], paths) -> int:
    from metasphere.project import project_for_scope
    if rest:
        rc = _reject_flag_shape(rest[0], "for")
        if rc is not None:
            return rc
    target = Path(rest[0]) if rest else Path.cwd()
    proj = project_for_scope(target, paths=paths)
    if proj is None:
        return 0
    print(proj.name)
    return 0


def _cmd_chat(rest: list[str], paths) -> int:
    from metasphere.project import get_project
    if rest:
        rc = _reject_flag_shape(rest[0], "chat")
        if rc is not None:
            return rc
    if len(rest) < 2:
        print("usage: project chat <name> 'message'", file=sys.stderr)
        return 2
    name, message = rest[0], " ".join(rest[1:])
    proj = get_project(name, paths=paths)
    if proj is None:
        print(f"project not found: {name}", file=sys.stderr)
        return 1
    if not proj.telegram_topic:
        print(
            f"project {proj.name!r} has no telegram topic. "
            f"Attach one with `metasphere project topic create {proj.name}` "
            f"(requires `metasphere telegram groups setup` first).",
            file=sys.stderr,
        )
        return 1
    from metasphere.telegram import groups as tg_groups
    try:
        tg_groups.send_to_topic(
            int(proj.telegram_topic["id"]), message,
            agent="@orchestrator", paths=paths,
        )
    except Exception as e:
        print(f"send failed: {e}", file=sys.stderr)
        return 1
    print("sent")
    return 0


def _cmd_topic(rest: list[str], paths) -> int:
    from metasphere.project import attach_topic
    if not rest or rest[0] not in ("create", "attach"):
        print("usage: project topic create <name>", file=sys.stderr)
        return 2
    if len(rest) < 2:
        print("usage: project topic create <name>", file=sys.stderr)
        return 2
    name = rest[1]
    try:
        proj = attach_topic(name, paths=paths)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    if proj.telegram_topic:
        print(f"topic: {proj.telegram_topic['name']} "
              f"(id={proj.telegram_topic['id']})")
    return 0


def _cmd_changelog(rest: list[str], paths) -> int:
    from metasphere.project import project_changelog
    if rest:
        rc = _reject_flag_shape(rest[0], "changelog")
        if rc is not None:
            return rc
    try:
        f = project_changelog(rest[0] if rest else None, paths=paths)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"wrote {f}")
    return 0


def _cmd_learnings(rest: list[str], paths) -> int:
    from metasphere.project import project_learnings
    if rest:
        rc = _reject_flag_shape(rest[0], "learnings")
        if rc is not None:
            return rc
    try:
        f = project_learnings(rest[0] if rest else None, paths=paths)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"wrote {f}")
    return 0


def _cmd_rename(rest: list[str], paths) -> int:
    from metasphere.project import rename_project, get_project

    if rest:
        rc = _reject_flag_shape(rest[0], "rename")
        if rc is not None:
            return rc
        if len(rest) > 1:
            rc = _reject_flag_shape(rest[1], "rename")
            if rc is not None:
                return rc
    if len(rest) < 2:
        print("Usage: metasphere project rename <old-name> <new-name>",
              file=sys.stderr)
        return 2
    old_name, new_name = rest[0], rest[1]

    if old_name == new_name:
        print(f"'{old_name}' is already named '{new_name}' — nothing to do.")
        return 0

    try:
        proj = rename_project(old_name, new_name, paths=paths)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1
    except FileExistsError as e:
        print(str(e), file=sys.stderr)
        return 1
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 1

    print(f"Renamed: {old_name} → {proj.name}")
    print(f"  Path: {proj.path}")

    # Best-effort scan for dangling references in agent persona files
    _warn_dangling_refs(old_name, paths)
    return 0


def _warn_dangling_refs(old_name: str, paths) -> None:
    """Scan agent persona-index/MISSION files for stale project refs."""
    agents_dir = paths.agents
    if not agents_dir.is_dir():
        return
    for agent_dir in agents_dir.iterdir():
        if not agent_dir.is_dir() or not agent_dir.name.startswith("@"):
            continue
        for fname in ("persona-index.md", "MISSION.md"):
            fp = agent_dir / fname
            if not fp.is_file():
                continue
            try:
                if old_name in fp.read_text(encoding="utf-8"):
                    print(f"  WARN: {agent_dir.name}/{fname} still references "
                          f"'{old_name}' — update manually")
            except OSError:
                pass


_DISPATCH = {
    "new":        _cmd_new,
    "init":       _cmd_init,
    "list":       _cmd_list,
    "ls":         _cmd_list,
    "show":       _cmd_show,
    "member":     _cmd_member,
    "members":    lambda r, p: _cmd_member(["list", *r], p),
    "wake":       _cmd_wake,
    "for":        _cmd_for,
    "chat":       _cmd_chat,
    "topic":      _cmd_topic,
    "changelog":  _cmd_changelog,
    "changes":    _cmd_changelog,
    "learnings":  _cmd_learnings,
    "learn":      _cmd_learnings,
    "rename":     _cmd_rename,
}


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] in ("--help", "-h"):
        sys.stdout.write(USAGE)
        return 0
    if not args:
        sys.stderr.write(USAGE)
        return 2
    cmd, *rest = args
    handler = _DISPATCH.get(cmd)
    if handler is None:
        print(f"unknown subcommand: {cmd}", file=sys.stderr)
        return 2
    return handler(rest, resolve())


if __name__ == "__main__":
    raise SystemExit(main())
