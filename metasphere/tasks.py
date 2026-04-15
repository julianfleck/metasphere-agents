"""Fractal task management.

Atomic IO, locking, and frontmatter parsing all live in
``metasphere.io`` — this module is a thin domain layer on top.
"""

from __future__ import annotations

import datetime as _dt
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from .identity import resolve_agent_id
from .io import (
    Frontmatter,
    atomic_write_text,
    file_lock,
    parse_frontmatter,
    serialize_frontmatter,
)


def _utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Slug sanitisation (strip slashes and special chars from task titles)
# ---------------------------------------------------------------------------

_SLUG_STRIP = re.compile(r"[^a-z0-9_-]+")


def slugify(text: str, max_len: int = 60) -> str:
    """Normalize a free-form title into a safe filename slug."""
    s = text.strip().lower()
    s = s.replace("/", "-")
    s = re.sub(r"\s+", "-", s)
    s = _SLUG_STRIP.sub("", s)
    s = re.sub(r"-{2,}", "-", s).strip("-")
    if not s:
        s = "task"
    return s[:max_len]


# ---------------------------------------------------------------------------
# Task model
# ---------------------------------------------------------------------------

VALID_PRIORITIES = ("!urgent", "!high", "!normal", "!low")
PRIORITY_DEFAULT = "!normal"

STATUS_PENDING = "pending"
STATUS_IN_PROGRESS = "in-progress"
STATUS_BLOCKED = "blocked"
STATUS_COMPLETED = "completed"
STATUS_ABANDONED = "abandoned"


@dataclass
class Task:
    id: str
    title: str
    priority: str = PRIORITY_DEFAULT
    status: str = STATUS_PENDING
    scope: str = "/"
    created: str = ""
    created_by: str = ""
    started: str = ""
    completed: str = ""
    updated: str = ""
    assignee: str = ""
    project: str = "default"
    last_pinged_at: str = ""
    ping_count: int = 0
    body: str = ""
    # runtime-only fields (excluded from serialisation)
    path: Path | None = field(default=None, repr=False, compare=False)

    @property
    def slug(self) -> str:
        return self.id

    # ---- (de)serialisation ----------------------------------------------

    def to_text(self) -> str:
        meta = {
            "id": self.id,
            "title": self.title,
            "priority": self.priority,
            "status": self.status,
            "scope": self.scope,
            "project": self.project,
            "created": self.created,
            "created_by": self.created_by,
            "assigned_to": self.assignee,
            "started_at": self.started,
            "updated_at": self.updated,
            "completed_at": self.completed,
            "last_pinged_at": self.last_pinged_at,
            "ping_count": self.ping_count,
        }
        return serialize_frontmatter(Frontmatter(meta, self.body))

    @classmethod
    def from_text(cls, text: str, path: Path | None = None) -> "Task":
        fm = parse_frontmatter(text)
        m = fm.meta
        def s(k: str, default: str = "") -> str:
            v = m.get(k, default)
            return "" if v is None else str(v)
        return cls(
            id=s("id"),
            title=s("title"),
            priority=s("priority", PRIORITY_DEFAULT) or PRIORITY_DEFAULT,
            status=s("status", STATUS_PENDING) or STATUS_PENDING,
            scope=s("scope", "/") or "/",
            project=s("project", "default") or "default",
            created=s("created"),
            created_by=s("created_by"),
            started=s("started_at"),
            updated=s("updated_at"),
            completed=s("completed_at"),
            assignee=s("assigned_to"),
            last_pinged_at=s("last_pinged_at"),
            ping_count=int(m.get("ping_count") or 0),
            body=fm.body,
            path=path,
        )


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def _canonical_tasks_dirs(paths: "Paths | None" = None) -> list[Path]:
    """Return every ``.tasks/`` dir the system owns.

    Canonical layout (Julian 2026-04-14):
    - ``~/.metasphere/projects/<name>/.tasks/`` for each registered project
    - ``~/.metasphere/tasks/`` for the global / unscoped bucket

    This is the single source of truth for "where tasks live" — task
    lookup (``_find_task_file``), consolidator scans, and new-task
    placement all route through it.
    """
    from .paths import resolve as _resolve
    paths = paths or _resolve()
    roots: list[Path] = []
    if paths.projects.is_dir():
        for entry in sorted(paths.projects.iterdir()):
            t = entry / ".tasks"
            if t.is_dir():
                roots.append(t)
    global_t = paths.root / "tasks"
    if global_t.is_dir():
        roots.append(global_t)
    return roots


def _project_tasks_dir(scope: Path, paths: "Paths | None" = None) -> Path:
    """Canonical ``.tasks/`` dir for a given scope.

    Walks the projects registry to match ``scope`` to a registered project.
    If matched: returns ``~/.metasphere/projects/<name>/.tasks/``.
    If not matched: returns the global ``~/.metasphere/tasks/`` bucket.

    Never returns a legacy ``<repo>/.tasks/`` path — by design. The
    migration subcommand moves legacy content into the canonical root
    once; after that, this function is the only path computer.
    """
    from .paths import resolve as _resolve
    from .project import Project
    paths = paths or _resolve()
    proj = Project.for_cwd(Path(scope), paths)
    if proj is not None and proj.name:
        return proj.tasks_dir(paths)
    return Project.global_scope().tasks_dir(paths)


def _active_dir(scope: Path, paths: "Paths | None" = None) -> Path:
    return _project_tasks_dir(scope, paths) / "active"


def _rel_to_repo(path: Path, project_root: Path) -> str:
    try:
        rel = path.resolve().relative_to(project_root.resolve())
        s = "/" + str(rel)
    except ValueError:
        s = str(path)
    return s.rstrip("/") or "/"


def _unique_slug(active: Path, base: str) -> str:
    candidate = base
    n = 2
    while (active / f"{candidate}.md").exists():
        candidate = f"{base}-{n}"
        n += 1
    return candidate


def _lock_path(task_path: Path) -> Path:
    """Sidecar lock file with a stable inode (never unlinked)."""
    return task_path.with_name(task_path.name + ".lock")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def _auto_project(scope: Path) -> str:
    """Determine project slug from scope via project_for_scope, else 'default'."""
    try:
        from .project import Project
        proj = Project.for_cwd(Path(scope))
        if proj is not None and proj.name:
            return proj.name
    except Exception:
        pass
    return "default"


def create_task(
    title: str,
    priority: str,
    scope: Path,
    project_root: Path,
    *,
    created_by: str | None = None,
    project: str | None = None,
    assigned_to: str | None = None,
) -> Task:
    """Create a new task at ``<scope>/.tasks/active/<slug>.md``.

    ``project`` defaults to the enclosing project (via
    ``project_for_scope``) or ``"default"``. ``assigned_to`` defaults to
    ``$METASPHERE_AGENT_ID`` (the resolving agent). Tasks should always
    have both; callers that can't determine an owner should explicitly
    pass ``"@unassigned"``.
    """
    if priority not in VALID_PRIORITIES:
        raise ValueError(f"invalid priority {priority!r}; want one of {VALID_PRIORITIES}")

    scope = Path(scope)
    project_root = Path(project_root)
    # Path computation now goes through ``_project_tasks_dir`` which
    # resolves scope → registered project → ``~/.metasphere/projects/
    # <name>/.tasks/`` (or the global bucket). ``scope`` is still used
    # below for the task's ``scope`` field (the user-visible label).
    active = _project_tasks_dir(scope) / "active"
    active.mkdir(parents=True, exist_ok=True)

    slug = _unique_slug(active, slugify(title))
    path = active / f"{slug}.md"

    import os as _os
    resolved_creator = created_by if created_by is not None else resolve_agent_id()
    if assigned_to is not None:
        resolved_assignee = assigned_to
    else:
        env_agent = _os.environ.get("METASPHERE_AGENT_ID", "").strip()
        resolved_assignee = env_agent  # empty string preserves legacy unowned semantics
    resolved_project = project if project is not None else _auto_project(scope)

    task = Task(
        id=slug,
        title=title,
        priority=priority,
        status=STATUS_PENDING,
        scope=_rel_to_repo(scope, project_root),
        project=resolved_project,
        created=_utcnow(),
        updated=_utcnow(),
        created_by=resolved_creator,
        assignee=resolved_assignee,
        body=f"\n# {title}\n\n## Updates\n\n- {_utcnow()} Created task\n",
        path=path,
    )
    atomic_write_text(path, task.to_text())
    return task


def assign_task(task_id: str, agent: str, project_root: Path) -> Task:
    """Retroactively set ``assigned_to`` on a task without changing status."""
    if not agent.startswith("@"):
        agent = "@" + agent
    return update_task(task_id, project_root, assigned_to=agent,
                       note=f"Assigned to {agent}")


def move_task_project(task_id: str, project: str, project_root: Path) -> Task:
    """Retroactively set the ``project`` field on a task."""
    return update_task(task_id, project_root, project=project,
                       note=f"Moved to project {project}")


def dispatch_task(
    title: str,
    agent_id: str,
    *,
    priority: str = "!normal",
    project: str = "",
    scope: Path | None = None,
    description: str = "",
) -> dict:
    """Create a task, wake the assigned agent, and send a !task message.

    This is the single entry point for delegating work to a project agent.
    Connects the task system, agent lifecycle, and message system in one
    atomic action.

    Returns a dict with keys: task, agent, message_id.
    """
    from . import agents as _agents
    from . import messages as _msg
    from .events import log_event
    from .paths import resolve

    paths = resolve()
    if not agent_id.startswith("@"):
        agent_id = "@" + agent_id

    # Resolve scope: project path if specified, else repo root
    if scope is None:
        if project:
            # Try to find the project's path
            try:
                from . import project as _proj
                proj = _proj.load_project(project, paths=paths)
                scope = Path(proj.path)
            except Exception:
                scope = paths.project_root
        else:
            scope = paths.project_root

    # 1. Create the task
    task = create_task(
        title=title,
        priority=priority,
        scope=scope,
        project_root=paths.project_root,
        assigned_to=agent_id,
        project=project or None,
    )

    # Append description to task body if provided
    if description:
        update_task(task.id, paths.project_root, note=f"Brief: {description}")

    # 2. Wake the agent if dormant
    agent_record = None
    try:
        agent_record = _agents.wake_persistent(agent_id, paths=paths)
    except ValueError:
        pass  # Not a persistent agent, or no MISSION.md — that's ok

    # 3. Send a !task message referencing the task ID
    message_body = f"[task:{task.id}] {title}"
    if description:
        message_body += f"\n\n{description}"
    message_id = ""
    try:
        msg = _msg.send_message(
            target=agent_id,
            label="task",
            body=message_body,
            from_agent="@orchestrator",
        )
        message_id = getattr(msg, "id", "")
    except Exception:
        pass  # Message send failure shouldn't block task creation

    # 4. Log the event
    log_event(
        "task.dispatch",
        f"Dispatched '{title}' to {agent_id} (task:{task.id})",
        agent="@orchestrator",
        paths=paths,
    )

    return {
        "task": task,
        "agent": agent_record,
        "message_id": message_id,
        "agent_id": agent_id,
        "project": project,
    }


def _find_task_file(task_id: str, *, include_completed: bool = True) -> Path | None:
    """Locate ``<task_id>.md`` across every canonical task dir.

    Searches ``~/.metasphere/projects/*/.tasks/`` and ``~/.metasphere/tasks/``.

    Each ``.tasks/`` is probed in order: ``active/<id>.md``, legacy
    ``completed/<id>.md``, then dated ``archive/YYYY-MM-DD/<id>.md``
    (newest-first).
    """
    for tasks_dir in _canonical_tasks_dirs():
        cand = tasks_dir / "active" / f"{task_id}.md"
        if cand.exists():
            return cand
        if not include_completed:
            continue
        cand = tasks_dir / "completed" / f"{task_id}.md"
        if cand.exists():
            return cand
        archive = tasks_dir / "archive"
        if archive.is_dir():
            for day in sorted(archive.iterdir(), reverse=True):
                if not day.is_dir():
                    continue
                cand = day / f"{task_id}.md"
                if cand.exists():
                    return cand
    return None


def _load(path: Path) -> Task:
    return Task.from_text(path.read_text(encoding="utf-8"), path=path)


def update_task(
    task_id: str,
    project_root: Path,
    **fields: str,
) -> Task:
    """Atomically rewrite frontmatter fields on a task file."""
    path = _find_task_file(task_id)
    if path is None:
        raise FileNotFoundError(f"task {task_id} not found under {project_root}")

    with file_lock(_lock_path(path)):
        task = _load(path)
        note = fields.pop("note", None)
        alias = {
            "started_at": "started",
            "completed_at": "completed",
            "assigned_to": "assignee",
        }
        for k, v in fields.items():
            attr = alias.get(k, k)
            if not hasattr(task, attr):
                raise AttributeError(f"Task has no field {attr!r}")
            setattr(task, attr, v)
        if note:
            task.body = _append_update(task.body, note)
        task.updated = _utcnow()
        atomic_write_text(path, task.to_text())
        return task


def _append_update(body: str, note: str) -> str:
    line = f"- {_utcnow()} {note}"
    if "## Updates" in body:
        return re.sub(
            r"(## Updates\n)",
            lambda m: m.group(1) + line + "\n",
            body,
            count=1,
        )
    return body.rstrip() + f"\n\n## Updates\n\n{line}\n"


def add_update(task_id: str, note: str, project_root: Path) -> Task:
    path = _find_task_file(task_id)
    if path is None:
        raise FileNotFoundError(f"task {task_id} not found")
    with file_lock(_lock_path(path)):
        task = _load(path)
        task.body = _append_update(task.body, note)
        task.updated = _utcnow()
        atomic_write_text(path, task.to_text())
        return task


def _replace_description(body: str, text: str) -> str:
    """Replace the body of the ``## Description`` section with ``text``.

    Matches the section that starts at ``## Description`` and ends at the
    next ``## `` heading (or end-of-file). Stub placeholder is overwritten.
    If no Description section exists, inserts one near the top.
    """
    if "## Description" in body:
        return re.sub(
            r"(## Description\n)(?:.*?)(?=\n## |\Z)",
            lambda m: m.group(1) + "\n" + text.strip() + "\n",
            body,
            count=1,
            flags=re.DOTALL,
        )
    # No Description section: insert after the first H1 (the title) or at top.
    if body.lstrip().startswith("# "):
        first_break = body.find("\n", body.find("# "))
        if first_break != -1:
            return (
                body[: first_break + 1]
                + f"\n## Description\n\n{text.strip()}\n"
                + body[first_break + 1 :]
            )
    return f"## Description\n\n{text.strip()}\n\n" + body


def set_description(task_id: str, text: str, project_root: Path) -> Task:
    """Replace the ``## Description`` section of a task with ``text``."""
    path = _find_task_file(task_id)
    if path is None:
        raise FileNotFoundError(f"task {task_id} not found")
    with file_lock(_lock_path(path)):
        task = _load(path)
        task.body = _replace_description(task.body, text)
        task.updated = _utcnow()
        atomic_write_text(path, task.to_text())
        return task


def start_task(task_id: str, agent: str, project_root: Path) -> Task:
    path = _find_task_file(task_id)
    if path is None:
        raise FileNotFoundError(f"task {task_id} not found")
    with file_lock(_lock_path(path)):
        task = _load(path)
        now = _utcnow()
        task.status = STATUS_IN_PROGRESS
        task.assignee = agent
        task.started = now
        task.updated = now
        task.body = _append_update(task.body, f"Started by {agent}")
        atomic_write_text(path, task.to_text())
        return task


def complete_task(task_id: str, summary: str, project_root: Path) -> Task:
    """Mark task complete and move file from ``active/`` → ``completed/``."""
    path = _find_task_file(task_id)
    if path is None:
        raise FileNotFoundError(f"task {task_id} not found")

    lock_src = _lock_path(path)
    with file_lock(lock_src):
        task = _load(path)
        now = _utcnow()
        task.status = STATUS_COMPLETED
        task.completed = now
        task.updated = now
        if summary:
            task.body = _append_update(task.body, f"Completed: {summary}")
        atomic_write_text(path, task.to_text())

        # active/<slug>.md → archive/YYYY-MM-DD/<slug>.md (dated daily bucket).
        # Legacy completed/ is left untouched for pre-cutover tasks; readers
        # (find_task, list_tasks) still include it.
        today = now[:10]  # YYYY-MM-DD
        archive_dir = path.parent.parent / "archive" / today
        archive_dir.mkdir(parents=True, exist_ok=True)
        dest = archive_dir / path.name
        shutil.move(str(path), str(dest))
        task.path = dest

    # Sidecar lock follows the task file so active/ doesn't accumulate orphans.
    # Done after the `with` block so the flock is released first.
    lock_dest = _lock_path(dest)
    if lock_src.exists() and not lock_dest.exists():
        try:
            shutil.move(str(lock_src), str(lock_dest))
        except OSError:
            pass
    return task


def abandon_task(task_id: str, reason: str, project_root: Path) -> Task:
    """Mark task abandoned and move file from ``active/`` → ``archive/_abandoned/``.

    Terminal state for orphan tasks that have aged past the abandon
    window without ever finding an owner. Mirrors :func:`complete_task`
    but uses a flat ``_abandoned`` bucket (no daily date dir) so the
    archive doesn't get noisy with one-task-per-day folders.
    """
    path = _find_task_file(task_id)
    if path is None:
        raise FileNotFoundError(f"task {task_id} not found")

    lock_src = _lock_path(path)
    with file_lock(lock_src):
        task = _load(path)
        now = _utcnow()
        task.status = STATUS_ABANDONED
        task.updated = now
        if reason:
            task.body = _append_update(task.body, f"Abandoned: {reason}")
        atomic_write_text(path, task.to_text())

        archive_dir = path.parent.parent / "archive" / "_abandoned"
        archive_dir.mkdir(parents=True, exist_ok=True)
        dest = archive_dir / path.name
        shutil.move(str(path), str(dest))
        task.path = dest

    lock_dest = _lock_path(dest)
    if lock_src.exists() and not lock_dest.exists():
        try:
            shutil.move(str(lock_src), str(lock_dest))
        except OSError:
            pass
    return task


def list_tasks(
    scope: Path,
    project_root: Path,
    include_completed: bool = False,
) -> list[Task]:
    """Collect tasks visible from ``scope``.

    In the canonical layout each project stores its tasks in exactly one
    place (``~/.metasphere/projects/<name>/.tasks/``) — the old "walk
    nested ``.tasks/`` dirs up to project_root" pattern no longer
    applies, because subdirectories don't carry their own task trees.

    Visibility reduces to "the project that owns this scope, plus the
    global / unscoped bucket." Scope-level filtering (if a caller wants
    only tasks whose ``scope`` field matches a path prefix) is the
    caller's responsibility.
    """
    from .paths import resolve as _resolve
    from .project import Project
    paths = _resolve()

    candidates: list[Project] = []
    proj = Project.for_cwd(Path(scope), paths)
    if proj is not None and proj.name:
        candidates.append(proj)
    candidates.append(Project.global_scope())

    seen: list[Task] = []
    for p in candidates:
        td = p.tasks_dir(paths)
        if not td.is_dir():
            continue
        dirs: list[Path] = [td / "active"]
        if include_completed:
            dirs.append(td / "completed")  # legacy pre-cutover
            archive = td / "archive"
            if archive.is_dir():
                dirs.extend(sorted(d for d in archive.iterdir() if d.is_dir()))
        for d in dirs:
            if not d.is_dir():
                continue
            for f in sorted(d.glob("*.md")):
                try:
                    seen.append(_load(f))
                except Exception:
                    continue
    return seen
