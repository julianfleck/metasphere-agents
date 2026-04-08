"""Fractal task management — Python port of scripts/tasks.

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
# Slug sanitisation (fixes the bash bug: slashes were preserved)
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


def _tasks_dir(scope_dir: Path) -> Path:
    return scope_dir / ".tasks"


def _active_dir(scope_dir: Path) -> Path:
    return _tasks_dir(scope_dir) / "active"


def _completed_dir(scope_dir: Path) -> Path:
    return _tasks_dir(scope_dir) / "completed"


def _rel_to_repo(path: Path, repo_root: Path) -> str:
    try:
        rel = path.resolve().relative_to(repo_root.resolve())
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


def create_task(
    title: str,
    priority: str,
    scope: Path,
    repo_root: Path,
    *,
    created_by: str | None = None,
) -> Task:
    """Create a new task at ``<scope>/.tasks/active/<slug>.md``."""
    if priority not in VALID_PRIORITIES:
        raise ValueError(f"invalid priority {priority!r}; want one of {VALID_PRIORITIES}")

    scope = Path(scope)
    repo_root = Path(repo_root)
    active = _active_dir(scope)
    active.mkdir(parents=True, exist_ok=True)

    slug = _unique_slug(active, slugify(title))
    path = active / f"{slug}.md"

    task = Task(
        id=slug,
        title=title,
        priority=priority,
        status=STATUS_PENDING,
        scope=_rel_to_repo(scope, repo_root),
        created=_utcnow(),
        updated=_utcnow(),
        created_by=created_by if created_by is not None else resolve_agent_id(),
        body=f"\n# {title}\n\n## Updates\n\n- {_utcnow()} Created task\n",
        path=path,
    )
    atomic_write_text(path, task.to_text())
    return task


def _find_task_file(task_id: str, repo_root: Path, *, include_completed: bool = True) -> Path | None:
    """Walk every ``.tasks/`` under ``repo_root`` looking for ``<task_id>.md``.

    Searches ``active/``, legacy ``completed/``, and dated ``archive/YYYY-MM-DD/``.
    """
    repo_root = Path(repo_root)
    for tasks_dir in repo_root.rglob(".tasks"):
        if not tasks_dir.is_dir():
            continue
        # active/ always
        cand = tasks_dir / "active" / f"{task_id}.md"
        if cand.exists():
            return cand
        if not include_completed:
            continue
        # legacy completed/
        cand = tasks_dir / "completed" / f"{task_id}.md"
        if cand.exists():
            return cand
        # dated archive/YYYY-MM-DD/
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
    repo_root: Path,
    **fields: str,
) -> Task:
    """Atomically rewrite frontmatter fields on a task file."""
    path = _find_task_file(task_id, repo_root)
    if path is None:
        raise FileNotFoundError(f"task {task_id} not found under {repo_root}")

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


def add_update(task_id: str, note: str, repo_root: Path) -> Task:
    path = _find_task_file(task_id, repo_root)
    if path is None:
        raise FileNotFoundError(f"task {task_id} not found")
    with file_lock(_lock_path(path)):
        task = _load(path)
        task.body = _append_update(task.body, note)
        task.updated = _utcnow()
        atomic_write_text(path, task.to_text())
        return task


def start_task(task_id: str, agent: str, repo_root: Path) -> Task:
    path = _find_task_file(task_id, repo_root)
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


def complete_task(task_id: str, summary: str, repo_root: Path) -> Task:
    """Mark task complete and move file from ``active/`` → ``completed/``."""
    path = _find_task_file(task_id, repo_root)
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


def list_tasks(
    scope: Path,
    repo_root: Path,
    include_completed: bool = False,
) -> list[Task]:
    """Collect tasks visible from ``scope`` (scope + parents up to repo root)."""
    scope = Path(scope).resolve()
    repo_root = Path(repo_root).resolve()
    seen: list[Task] = []

    current = scope
    while True:
        td = current / ".tasks"
        if td.is_dir():
            dirs: list[Path] = [td / "active"]
            if include_completed:
                dirs.append(td / "completed")  # legacy pre-cutover
                archive = td / "archive"
                if archive.is_dir():
                    dirs.extend(sorted(p for p in archive.iterdir() if p.is_dir()))
            for d in dirs:
                if d.is_dir():
                    for f in sorted(d.glob("*.md")):
                        try:
                            seen.append(_load(f))
                        except Exception:
                            continue
        if current == repo_root or repo_root not in current.parents:
            break
        current = current.parent

    return seen
