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
    filter_ = args[0] if args else "active"
    include_completed = filter_ in ("all", "completed")
    scope, repo = _ctx()
    items = _tasks.list_tasks(scope, repo, include_completed=include_completed)
    if filter_ == "completed":
        items = [t for t in items if t.status == _tasks.STATUS_COMPLETED]
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
        print(f"  {t.scope} | {t.status}")
    return 0


def _cmd_new(args: list[str]) -> int:
    priority = _tasks.PRIORITY_DEFAULT
    title_parts: list[str] = []
    for a in args:
        if a in _tasks.VALID_PRIORITIES:
            priority = a
        else:
            title_parts.append(a)
    title = " ".join(title_parts)
    if not title:
        print('Usage: tasks new "title" [!priority]', file=sys.stderr)
        return 1
    scope, repo = _ctx()
    t = _tasks.create_task(title, priority, scope, repo)
    print(f"Created task: {t.id}")
    print(f"  Title: {t.title}")
    print(f"  Priority: {t.priority}")
    print(f"  File: {t.path}")
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
        return 1
    task_id, *rest = args
    summary = " ".join(rest)
    _, repo = _ctx()
    t = _tasks.complete_task(task_id, summary, repo)
    print(f"Completed: {t.id}")
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
    if not argv or argv[0] == "list":
        return _cmd_list(argv[1:] if argv else [])
    cmd, rest = argv[0], argv[1:]
    handlers = {
        "new": _cmd_new,
        "start": _cmd_start,
        "update": _cmd_update,
        "done": _cmd_done,
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
