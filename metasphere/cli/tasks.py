"""CLI shim mirroring the bash ``scripts/tasks`` command surface.

Usage:
    python -m metasphere.cli.tasks                       # list active
    python -m metasphere.cli.tasks list [all|completed]
    python -m metasphere.cli.tasks new "title" [!priority]
    python -m metasphere.cli.tasks start <task-id>
    python -m metasphere.cli.tasks update <task-id> "note"
    python -m metasphere.cli.tasks done <task-id> "summary"
    python -m metasphere.cli.tasks show <task-id>
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from metasphere import paths as _paths
from metasphere import tasks as _tasks
from metasphere.identity import resolve_agent_id


def _ctx() -> tuple[Path, Path]:
    p = _paths.resolve()
    return p.scope, p.repo


def _agent() -> str:
    return resolve_agent_id(_paths.resolve())


def _cmd_list(args: list[str]) -> int:
    # Parse positional filter + long-flag filters
    filter_ = "active"
    unassigned = False
    project_filter: str | None = None
    owner_filter: str | None = None
    rest = list(args)
    i = 0
    while i < len(rest):
        a = rest[i]
        if a == "--unassigned":
            unassigned = True
            i += 1
        elif a == "--project" and i + 1 < len(rest):
            project_filter = rest[i + 1]
            i += 2
        elif a == "--owner" and i + 1 < len(rest):
            owner_filter = rest[i + 1]
            i += 2
        elif a in ("active", "all", "completed"):
            filter_ = a
            i += 1
        else:
            i += 1
    include_completed = filter_ in ("all", "completed")
    scope, repo = _ctx()
    items = _tasks.list_tasks(scope, repo, include_completed=include_completed)
    if filter_ == "completed":
        items = [t for t in items if t.status == _tasks.STATUS_COMPLETED]
    if unassigned:
        items = [t for t in items if not t.assignee or t.assignee == "@unassigned"]
    if project_filter is not None:
        items = [t for t in items if (t.project or "default") == project_filter]
    if owner_filter is not None:
        owner_norm = owner_filter if owner_filter.startswith("@") else "@" + owner_filter
        items = [t for t in items if t.assignee == owner_norm]
    if not items:
        print(f"## Tasks: No {filter_} tasks in scope")
        return 0
    print(f"## Tasks ({scope})")
    print()
    for t in items:
        icon = {
            "pending": "○",
            "in-progress": "◐",
            "blocked": "◼",
            "completed": "●",
        }.get(t.status, "?")
        suffix = f" → {t.assignee}" if t.assignee else ""
        print(f"{icon} {t.priority} {t.title} [{t.id}]{suffix}")
        lifecycle = ""
        if t.created:
            lifecycle = f"created {t.created[:10]}"
            if t.updated and t.updated[:10] != t.created[:10]:
                lifecycle += f", updated {t.updated[:10]}"
        if lifecycle:
            print(f"  {t.scope} | {t.status} | {lifecycle}")
        else:
            print(f"  {t.scope} | {t.status}")
    return 0


def _cmd_new(args: list[str]) -> int:
    priority = _tasks.PRIORITY_DEFAULT
    explicit_project: str | None = None
    explicit_assign: str | None = None
    title_parts: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a in _tasks.VALID_PRIORITIES:
            priority = a
            i += 1
        elif a == "--project" and i + 1 < len(args):
            explicit_project = args[i + 1]
            i += 2
        elif a == "--assign" and i + 1 < len(args):
            explicit_assign = args[i + 1]
            i += 2
        else:
            title_parts.append(a)
            i += 1
    title = " ".join(title_parts)
    if not title:
        print(
            'Usage: tasks new "title" [!priority] [--project name] [--assign @agent]',
            file=sys.stderr,
        )
        return 1

    scope, repo = _ctx()

    # Soft enforcement: warn if auto-fill can't determine owner/project.
    auto_project = _tasks._auto_project(scope)
    auto_owner = os.environ.get("METASPHERE_AGENT_ID", "").strip()
    project = explicit_project
    if project is None and auto_project == "default":
        if explicit_project is None:
            print(
                "warning: no --project given and scope is not inside a registered "
                "project; filing under 'default'",
                file=sys.stderr,
            )
        project = "default"
    assigned = explicit_assign
    if assigned is None and not auto_owner:
        print(
            "warning: no --assign given and METASPHERE_AGENT_ID unset; "
            "assigning '@unassigned'",
            file=sys.stderr,
        )
        assigned = "@unassigned"
    if assigned and not assigned.startswith("@"):
        assigned = "@" + assigned

    t = _tasks.create_task(
        title, priority, scope, repo,
        project=project, assigned_to=assigned,
    )
    print(f"Created task: {t.id}")
    print(f"  Title: {t.title}")
    print(f"  Priority: {t.priority}")
    print(f"  Project: {t.project}")
    print(f"  Assigned: {t.assignee or '(none)'}")
    print(f"  File: {t.path}")
    return 0


def _cmd_assign(args: list[str]) -> int:
    if len(args) < 2:
        print("Usage: tasks assign <task-id> @agent", file=sys.stderr)
        return 1
    task_id, agent = args[0], args[1]
    _, repo = _ctx()
    t = _tasks.assign_task(task_id, agent, repo)
    print(f"Assigned: {t.id} → {t.assignee}")
    return 0


def _cmd_move(args: list[str]) -> int:
    # Usage: tasks move <task-id> --project <name>
    if not args or "--project" not in args:
        print("Usage: tasks move <task-id> --project <name>", file=sys.stderr)
        return 1
    task_id = args[0]
    try:
        idx = args.index("--project")
        project = args[idx + 1]
    except (ValueError, IndexError):
        print("Usage: tasks move <task-id> --project <name>", file=sys.stderr)
        return 1
    _, repo = _ctx()
    t = _tasks.move_task_project(task_id, project, repo)
    print(f"Moved: {t.id} → project={t.project}")
    return 0


def _cmd_start(args: list[str]) -> int:
    if not args:
        print("Usage: tasks start <task-id>", file=sys.stderr)
        return 1
    _, repo = _ctx()
    t = _tasks.start_task(args[0], _agent(), repo)
    print(f"Started: {t.id}")
    print(f"Assigned to: {t.assignee}")
    return 0


def _cmd_update(args: list[str]) -> int:
    if len(args) < 2:
        print('Usage: tasks update <task-id> "note"', file=sys.stderr)
        return 1
    task_id, *rest = args
    note = " ".join(rest)
    _, repo = _ctx()
    _tasks.add_update(task_id, note, repo)
    print(f"Updated: {task_id}")
    print(f"Note: {note}")
    return 0


def _cmd_done(args: list[str]) -> int:
    if not args:
        print('Usage: tasks done <task-id> ["summary"]', file=sys.stderr)
        print('       tasks archive <task-id> ["summary"]   (alias)', file=sys.stderr)
        return 1
    task_id, *rest = args
    summary = " ".join(rest)
    _, repo = _ctx()
    t = _tasks.complete_task(task_id, summary, repo)
    dest = t.path
    if dest is not None:
        print(f"Archived: {t.id} → {dest}")
    else:
        print(f"Archived: {t.id}")
    if summary:
        print(f"Summary: {summary}")
    return 0


def _cmd_show(args: list[str]) -> int:
    if not args:
        print("Usage: tasks show <task-id>", file=sys.stderr)
        return 1
    _, repo = _ctx()
    path = _tasks._find_task_file(args[0], repo)
    if path is None:
        print(f"Task {args[0]} not found", file=sys.stderr)
        return 1
    print(path.read_text())
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("--help", "-h"):
        print(__doc__ or "")
        return 0
    if not argv or argv[0] == "list":
        return _cmd_list(argv[1:] if argv else [])
    cmd, rest = argv[0], argv[1:]
    handlers = {
        "new": _cmd_new,
        "assign": _cmd_assign,
        "move": _cmd_move,
        "start": _cmd_start,
        "update": _cmd_update,
        "done": _cmd_done,
        "archive": _cmd_done,
        "show": _cmd_show,
        "all": lambda _r: _cmd_list(["all"]),
    }
    h = handlers.get(cmd)
    if not h:
        print(f"unknown command: {cmd}", file=sys.stderr)
        return 1
    return h(rest)


if __name__ == "__main__":
    raise SystemExit(main())
