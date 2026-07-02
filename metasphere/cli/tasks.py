"""``metasphere task`` — task lifecycle CLI shim.

Front-end onto ``metasphere.tasks``: create, list, update status, and
complete tasks scoped to a project or to ``/``. Tasks are the
medium-lived work-units between fire-and-forget messages and
multi-day project artifacts; the consolidate sweep escalates or
archives them based on age + status. State writes flow through the
tasks module; this shim only parses argv and renders.
"""

from __future__ import annotations

DESCRIPTION = "Create, list, update, and complete metasphere tasks."

USAGE = """\
Usage: metasphere task [<command> [args...]]

With no arguments, lists active tasks for the current scope. Commands:

  metasphere task list [all|completed]    Filter by status.
  metasphere task new "title" [!priority] Create a new task.
  metasphere task start <task-id>         Mark a task in-progress.
  metasphere task update <task-id> "note" Append a progress note.
  metasphere task done <task-id> "summary"
                                          Mark a task complete.
  metasphere task park <task-id> <wake-after-iso> ["trigger"]
                                          Park a trigger-gated task: the
                                          consolidator won't ping/escalate/
                                          abandon it until wake-after passes
                                          (plus a grace if a trigger is set).
  metasphere task unpark <task-id>        Clear wake-after/trigger (resume).
  metasphere task show <task-id>          Print one task in full.

Priorities: `!urgent`, `!high`, `!normal` (default), `!low`.
Tasks are stored under `.tasks/active/` at the current scope and
move to `.tasks/archive/YYYY-MM-DD/` on `done`.
"""


import os
import sys
from pathlib import Path

from metasphere import paths as _paths
from metasphere import tasks as _tasks
from metasphere.identity import resolve_agent_id


def _ctx() -> tuple[Path, Path]:
    p = _paths.resolve()
    return p.scope, p.repo


def _project_path_from_registry(name: str) -> Path | None:
    """Look up a registered project path by name. Returns None if unknown.

    Delegates to ``metasphere.project._find_project`` so we don't duplicate
    the registry-walk logic. Only the registry lookup branch is relevant
    here — the CWD-ancestry fallback is filtered out by passing a truthy
    name.
    """
    try:
        from metasphere.project import _find_project
    except Exception:
        return None
    paths = _paths.resolve()
    p = _find_project(name, paths)
    if p is None:
        return None
    p = Path(p)
    # _find_project never returns a non-registry result when ``name`` is
    # truthy, but double-check the directory exists before trusting it.
    return p if p.is_dir() else None


def _maybe_redirect_to_project(project_filter: str | None,
                               scope: Path, repo: Path) -> tuple[Path, Path]:
    """If ``--project`` names a registered project outside the current scope,
    redirect (scope, repo) to that project so CWD-scoped task I/O reads
    the right ``.tasks/`` directory. CWD-inside-project is a no-op.
    """
    if not project_filter:
        return scope, repo
    registered = _project_path_from_registry(project_filter)
    if registered is None:
        return scope, repo
    try:
        scope_resolved = Path(scope).resolve()
        reg_resolved = registered.resolve()
    except OSError:
        return scope, repo
    # Already inside the named project — nothing to do.
    if scope_resolved == reg_resolved or reg_resolved in scope_resolved.parents:
        return scope, repo
    return reg_resolved, reg_resolved


def _agent() -> str:
    return resolve_agent_id(_paths.resolve())


_TASK_ID_USAGE = {
    "start": "Use: metasphere task start <task-id>",
    "update": 'Use: metasphere task update <task-id> "note"',
    "done": 'Use: metasphere task done <task-id> ["summary"]',
    "describe": 'Use: metasphere task describe <task-id> "description text"',
    "show": "Use: metasphere task show <task-id>",
}


def _reject_flag_shape_task_id(value: str, op: str) -> int | None:
    """Return rc=1 + print error if ``value`` looks like a leaked CLI flag.

    Mirrors the guard in ``cli/messages.py``: task ids never start with
    ``-``. ``metasphere task start --bogus`` previously dropped an uncaught
    FileNotFoundError traceback all the way to the user; gate it here
    with a clean rc + usage hint instead.
    """
    if value.startswith("-"):
        hint = _TASK_ID_USAGE.get(op, "")
        msg = (
            f"Error: task-id {value!r} looks like a flag — `metasphere task {op}` "
            "takes positional args only."
        )
        if hint:
            msg = f"{msg} {hint}"
        print(msg, file=sys.stderr)
        return 1
    return None


def _scope_is_in_registered_project(scope: Path) -> bool:
    """Return True iff ``scope`` sits inside a directory with .metasphere/.

    Used to detect "we have no project context" so we can fall through to
    the all-projects condensed view. We intentionally avoid calling the
    registry here — all that matters is whether the CWD/scope is itself
    inside a project. A CWD of ``~/.metasphere`` (the gateway's home)
    qualifies as "no project context" even though ``.metasphere`` exists
    within it, because the scaffold under ``$METASPHERE_DIR`` is runtime
    state, not a project.
    """
    try:
        from metasphere.project import project_for_scope
        from metasphere import paths as _p
        paths = _p.resolve()
        # If scope is inside METASPHERE_DIR, treat as "no project context".
        try:
            if Path(scope).resolve() == paths.root.resolve() or \
               paths.root.resolve() in Path(scope).resolve().parents:
                return False
        except OSError:
            pass
        return project_for_scope(Path(scope)) is not None
    except Exception:
        return False


def _all_projects_tasks(include_completed: bool = False) -> list:
    """Walk the project registry and collect tasks from every project.

    Returns a flat list of :class:`metasphere.tasks.Task` objects. Any
    project whose ``.tasks/`` directory is missing or unreadable is
    silently skipped — a condensed view with N-1 projects is vastly
    preferable to a hard error.

    Deduplicates by task ID so that global tasks (scope ``/.``, project
    ``default``) appear once under a "Global" project header rather
    than being duplicated into every project section (each project's
    ``list_tasks`` includes the global bucket).
    """
    try:
        from metasphere.project import list_projects
        from metasphere import paths as _p
    except Exception:
        return []
    paths = _p.resolve()
    out: list = []
    seen_ids: set[str] = set()
    try:
        projects = list_projects(paths=paths)
    except Exception:
        return out
    for proj in projects:
        pp = Path(proj.path) if proj.path else None
        if pp is None or not pp.is_dir():
            continue
        try:
            items = _tasks.list_tasks(pp, pp, include_completed=include_completed)
        except Exception:
            continue
        for t in items:
            tid = getattr(t, "id", None)
            if tid and tid in seen_ids:
                continue
            if tid:
                seen_ids.add(tid)
            # Tag the project name onto each task in case the frontmatter
            # says "default" — keeps the condensed view accurate. Tasks
            # whose project is genuinely "default" (global scope) keep
            # that tag so format_task_condensed groups them under "Global".
            if not getattr(t, "project", None) or t.project == "default":
                t.project = proj.name
            out.extend([t])
    # Also collect global-only tasks not yet seen (tasks created at root
    # scope that don't appear under any registered project).
    try:
        from metasphere.project import Project
        global_proj = Project.global_scope()
        td = global_proj.tasks_dir(paths)
        if td.is_dir():
            for d in [td / "active"] + ([td / "completed"] if include_completed else []):
                if not d.is_dir():
                    continue
                for f in sorted(d.glob("*.md")):
                    try:
                        t = _tasks._load(f)
                    except Exception:
                        continue
                    tid = getattr(t, "id", None)
                    if tid and tid in seen_ids:
                        continue
                    if tid:
                        seen_ids.add(tid)
                    if not getattr(t, "project", None) or t.project == "default":
                        t.project = "Global"
                    out.append(t)
    except Exception:
        pass
    return out


def _cmd_list(args: list[str]) -> int:
    # Parse positional filter + long-flag filters
    filter_ = "active"
    unassigned = False
    project_filter: str | None = None
    owner_filter: str | None = None
    condensed = False
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
        elif a in ("--condensed", "-c"):
            condensed = True
            i += 1
        elif a in ("active", "all", "completed"):
            filter_ = a
            i += 1
        elif not a.startswith("-") and project_filter is None:
            project_filter = a
            i += 1
        else:
            i += 1
    include_completed = filter_ in ("all", "completed")
    scope, repo = _ctx()

    # All-projects fallback: no --project, no owner/unassigned filter, and
    # the CWD isn't inside any registered project. Walk the registry and
    # render condensed. This is the "bare ``metasphere task list`` from
    # ~/.metasphere" case that backs Telegram's bare /tasks.
    all_projects_mode = (
        project_filter is None
        and owner_filter is None
        and not unassigned
        and not _scope_is_in_registered_project(scope)
    )
    if all_projects_mode:
        items = _all_projects_tasks(include_completed=include_completed)
        if filter_ == "completed":
            items = [t for t in items if t.status == _tasks.STATUS_COMPLETED]
        elif filter_ == "active":
            items = [t for t in items
                     if t.status in (_tasks.STATUS_PENDING,
                                     _tasks.STATUS_IN_PROGRESS,
                                     _tasks.STATUS_BLOCKED)]
        if not items:
            print("Tasks: no active tasks across any registered project")
            return 0
        from metasphere.format import format_task_condensed
        print(format_task_condensed(items))
        return 0

    # If --project names a registered project and we're running from a CWD
    # outside that project (e.g. the Telegram gateway's CWD has no .tasks/),
    # redirect (scope, repo) to the project's path so list_tasks finds its
    # .tasks/ directory. The post-discovery project filter still runs below
    # as a safety net.
    scope, repo = _maybe_redirect_to_project(project_filter, scope, repo)
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
        print(f"Tasks: no {filter_} tasks in scope")
        return 0
    if condensed:
        from metasphere.format import format_task_condensed
        print(format_task_condensed(items))
        return 0
    from metasphere.format import format_task_table
    print(f"Tasks ({scope}) — {len(items)} {filter_}")
    print()
    print(format_task_table(items))
    return 0


def _cmd_new(args: list[str]) -> int:
    if args and args[0] in ("--help", "-h"):
        sys.stdout.write(
            'Usage: metasphere task new "title" [!priority] '
            "[--project <name>] [--assign @agent]\n"
        )
        return 0
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
        elif a in ("--project", "--assign"):
            # Consume-next-arg flags. A bare flag at the end or one
            # immediately followed by another flag-shaped token is a
            # typo we used to swallow into the title; reject so the
            # value doesn't land in task frontmatter as e.g.
            # ``project: --bogus`` or ``assigned: @--bogus``.
            if i + 1 >= len(args):
                print(
                    f"Error: {a} requires a value",
                    file=sys.stderr,
                )
                return 2
            value = args[i + 1]
            if value.startswith("-"):
                print(
                    f"Error: {a} value {value!r} looks like a flag; "
                    "expected a name",
                    file=sys.stderr,
                )
                return 2
            if a == "--project":
                explicit_project = value
            else:
                explicit_assign = value
            i += 2
        elif a.startswith("-"):
            # Unknown flag-shaped positional. Used to be silently appended
            # to the title — ``task new --boogus "x"`` would create a task
            # titled ``--boogus x`` and slug ``boogus-x``. Reject so typos
            # surface instead of polluting the task store.
            print(
                f"Error: unknown flag {a!r}; expected a title, a "
                f"priority ({', '.join(_tasks.VALID_PRIORITIES)}), "
                "--project, or --assign",
                file=sys.stderr,
            )
            return 2
        else:
            title_parts.append(a)
            i += 1
    title = " ".join(title_parts)
    if not title:
        print(
            'Usage: metasphere task new "title" [!priority] [--project name] [--assign @agent]',
            file=sys.stderr,
        )
        return 1

    scope, repo = _ctx()
    # If --project names a registered project and CWD is outside it, resolve
    # to that project's path so the task file lands in the right .tasks/.
    scope, repo = _maybe_redirect_to_project(explicit_project, scope, repo)

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
        print("Usage: metasphere task assign <task-id> @agent", file=sys.stderr)
        return 1
    task_id, agent = args[0], args[1]
    _, repo = _ctx()
    try:
        t = _tasks.assign_task(task_id, agent, repo)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    print(f"Assigned: {t.id} → {t.assignee}")
    return 0


def _cmd_move(args: list[str]) -> int:
    # Usage: metasphere task move <task-id> --project <name>
    if not args or "--project" not in args:
        print("Usage: metasphere task move <task-id> --project <name>", file=sys.stderr)
        return 1
    task_id = args[0]
    try:
        idx = args.index("--project")
        project = args[idx + 1]
    except (ValueError, IndexError):
        print("Usage: metasphere task move <task-id> --project <name>", file=sys.stderr)
        return 1
    _, repo = _ctx()
    try:
        t = _tasks.move_task_project(task_id, project, repo)
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    print(f"Moved: {t.id} → project={t.project}")
    return 0


def _cmd_start(args: list[str]) -> int:
    if not args:
        print("Usage: metasphere task start <task-id>", file=sys.stderr)
        return 1
    rc = _reject_flag_shape_task_id(args[0], "start")
    if rc is not None:
        return rc
    _, repo = _ctx()
    try:
        t = _tasks.start_task(args[0], _agent(), repo)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"Started: {t.id}")
    print(f"Assigned to: {t.assignee}")
    return 0


def _cmd_update(args: list[str]) -> int:
    if len(args) < 2:
        print('Usage: metasphere task update <task-id> "note"', file=sys.stderr)
        return 1
    task_id, *rest = args
    rc = _reject_flag_shape_task_id(task_id, "update")
    if rc is not None:
        return rc
    note = " ".join(rest)
    _, repo = _ctx()
    try:
        _tasks.add_update(task_id, note, repo)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"Updated: {task_id}")
    print(f"Note: {note}")
    return 0


def _cmd_done(args: list[str]) -> int:
    if not args:
        print('Usage: metasphere task done <task-id> ["summary"]', file=sys.stderr)
        print('       metasphere task archive <task-id> ["summary"]   (alias)', file=sys.stderr)
        return 1
    task_id, *rest = args
    rc = _reject_flag_shape_task_id(task_id, "done")
    if rc is not None:
        return rc
    summary = " ".join(rest)
    _, repo = _ctx()
    try:
        t = _tasks.complete_task(task_id, summary, repo)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1
    dest = t.path
    if dest is not None:
        print(f"Archived: {t.id} → {dest}")
    else:
        print(f"Archived: {t.id}")
    if summary:
        print(f"Summary: {summary}")
    return 0


def _cmd_park(args: list[str]) -> int:
    if len(args) < 2:
        print('Usage: metasphere task park <task-id> <wake-after-iso> ["trigger"]',
              file=sys.stderr)
        return 1
    task_id, wake_after, *rest = args
    rc = _reject_flag_shape_task_id(task_id, "park")
    if rc is not None:
        return rc
    trigger = " ".join(rest)
    _, repo = _ctx()
    note = f"Parked until {wake_after}" + (f" (trigger: {trigger})" if trigger else "")
    try:
        t = _tasks.update_task(
            task_id, repo, wake_after=wake_after, trigger=trigger, note=note,
        )
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"Parked: {t.id} (wake_after={t.wake_after})")
    if trigger:
        print(f"Trigger: {trigger}")
    return 0


def _cmd_unpark(args: list[str]) -> int:
    if not args:
        print("Usage: metasphere task unpark <task-id>", file=sys.stderr)
        return 1
    rc = _reject_flag_shape_task_id(args[0], "unpark")
    if rc is not None:
        return rc
    _, repo = _ctx()
    try:
        t = _tasks.update_task(
            args[0], repo, wake_after="", trigger="",
            note="Unparked: resumed normal lifecycle",
        )
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"Unparked: {t.id}")
    return 0


def _cmd_describe(args: list[str]) -> int:
    if len(args) < 2:
        print('Usage: metasphere task describe <task-id> "description text"', file=sys.stderr)
        return 1
    task_id, *rest = args
    rc = _reject_flag_shape_task_id(task_id, "describe")
    if rc is not None:
        return rc
    text = " ".join(rest)
    _, repo = _ctx()
    try:
        t = _tasks.set_description(task_id, text, repo)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"Described: {t.id}")
    return 0


def _cmd_show(args: list[str]) -> int:
    if not args:
        print("Usage: metasphere task show <task-id>", file=sys.stderr)
        return 1
    rc = _reject_flag_shape_task_id(args[0], "show")
    if rc is not None:
        return rc
    _, repo = _ctx()
    path = _tasks._find_task_file(args[0])
    if path is None:
        print(f"Task {args[0]} not found", file=sys.stderr)
        return 1
    print(path.read_text())
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("--help", "-h"):
        sys.stdout.write(USAGE)
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
        "describe": _cmd_describe,
        "description": _cmd_describe,
        "done": _cmd_done,
        "archive": _cmd_done,
        "park": _cmd_park,
        "unpark": _cmd_unpark,
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
