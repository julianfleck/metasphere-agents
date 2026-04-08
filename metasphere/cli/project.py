"""CLI: ``metasphere project ...``.

Subcommands::

    project new <name> [--path P] [--goal "..."] [--repo URL] [--member @x:role[:persistent]] ...
    project init [path]                       # legacy minimal constructor
    project list
    project show [name]
    project member add <name> @agent [--role R] [--persistent]
    project member remove <name> @agent
    project member list [name]
    project members [name]                    # alias for member list
    project wake [name]
    project for [path]                        # print enclosing project name
    project chat <name> "message"             # send to project telegram topic
    project changelog [name]
    project learnings [name]

Every subcommand flows through this dispatcher; lazy-imports the heavy
modules so help remains cheap.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from metasphere.paths import resolve


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
    except FileExistsError as e:
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
    target = Path(rest[0]) if rest else Path.cwd()
    p = init_project(path=target, paths=paths)
    print(f"Initialized project: {p.name}")
    print(f"  Path: {p.path}")
    return 0


def _cmd_list(rest: list[str], paths) -> int:
    from metasphere.project import list_projects
    rows = list_projects(paths=paths)
    if not rows:
        print("(no projects)")
        return 0
    for p in rows:
        print(f"{p.name}\t{p.status}\t{p.path}")
    return 0


def _cmd_show(rest: list[str], paths) -> int:
    from metasphere.project import get_project, project_for_scope
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
        proj = add_member(ns.name, ns.agent, role=ns.role,
                          persistent=ns.persistent, paths=paths)
        print(f"Added {ns.agent} to {proj.name}")
        return 0
    if verb == "remove":
        ap = argparse.ArgumentParser(prog="project member remove")
        ap.add_argument("name")
        ap.add_argument("agent")
        ns = ap.parse_args(args)
        proj = remove_member(ns.name, ns.agent, paths=paths)
        print(f"Removed {ns.agent} from {proj.name}")
        return 0
    if verb in ("list", "ls"):
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
    target = Path(rest[0]) if rest else Path.cwd()
    proj = project_for_scope(target, paths=paths)
    if proj is None:
        return 0
    print(proj.name)
    return 0


def _cmd_chat(rest: list[str], paths) -> int:
    from metasphere.project import get_project
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
    try:
        f = project_changelog(rest[0] if rest else None, paths=paths)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"wrote {f}")
    return 0


def _cmd_learnings(rest: list[str], paths) -> int:
    from metasphere.project import project_learnings
    try:
        f = project_learnings(rest[0] if rest else None, paths=paths)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"wrote {f}")
    return 0


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
}


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] in ("--help", "-h"):
        print(__doc__ or "")
        return 0
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    cmd, *rest = args
    handler = _DISPATCH.get(cmd)
    if handler is None:
        print(f"unknown subcommand: {cmd}", file=sys.stderr)
        return 2
    return handler(rest, resolve())


if __name__ == "__main__":
    raise SystemExit(main())
