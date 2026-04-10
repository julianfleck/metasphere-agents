"""Project management (port of scripts/metasphere-project, schema v2).

Projects are directories marked by ``.metasphere/`` containing
``project.json``. Schema v2 adds ``goal``, ``repo``, ``members[]``,
``links{}``, ``telegram_topic`` and a ``schema`` version field. Old v1
project files load with members=[], goal=None, repo=None and are
automatically migrated to v2 on the next save.
"""

from __future__ import annotations

import datetime as _dt
import json
import logging
import shutil
import subprocess

logger = logging.getLogger(__name__)
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from .io import atomic_write_text, read_json, write_json
from .paths import Paths, resolve


SCHEMA_VERSION = 2


@dataclass
class Member:
    id: str
    role: str = "contributor"
    persistent: bool = False
    spec: str = ""

    def to_dict(self) -> dict:
        d = {"id": self.id, "role": self.role, "persistent": self.persistent}
        if self.spec:
            d["spec"] = self.spec
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Member":
        return cls(
            id=str(d.get("id", "")),
            role=str(d.get("role", "contributor")),
            persistent=bool(d.get("persistent", False)),
            spec=str(d.get("spec", "")),
        )


@dataclass
class Project:
    name: str
    path: str
    created: str = ""
    status: str = "active"
    schema: int = SCHEMA_VERSION
    goal: Optional[str] = None
    repo: Optional[dict] = None
    members: list[Member] = field(default_factory=list)
    links: dict = field(default_factory=dict)
    telegram_topic: Optional[dict] = None  # {"id": int, "name": str}

    # Runtime; not serialized.
    scope: str = ""

    def to_dict(self) -> dict:
        d: dict[str, Any] = {
            "schema": self.schema,
            "name": self.name,
            "path": self.path,
            "created": self.created,
            "status": self.status,
            "goal": self.goal,
            "repo": self.repo,
            "members": [m.to_dict() for m in self.members],
            "links": self.links,
            "telegram_topic": self.telegram_topic,
        }
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "Project":
        members_raw = d.get("members") or []
        return cls(
            name=str(d.get("name", "")),
            path=str(d.get("path", "")),
            created=str(d.get("created", "")),
            status=str(d.get("status", "active")),
            schema=int(d.get("schema", 1)),
            goal=d.get("goal"),
            repo=d.get("repo"),
            members=[Member.from_dict(m) for m in members_raw],
            links=d.get("links") or {},
            telegram_topic=d.get("telegram_topic"),
        )


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _projects_file(paths: Paths) -> Path:
    return paths.root / "projects.json"


def _load_registry(paths: Paths) -> list[dict]:
    return read_json(_projects_file(paths), default=[]) or []


# ---------------------------------------------------------------------------
# Project file I/O
# ---------------------------------------------------------------------------


def _project_file(project_path: Path) -> Path:
    return project_path / ".metasphere" / "project.json"


def load_project(project_path: Path) -> Optional[Project]:
    """Load a project from its directory. Returns None if not present."""
    pf = _project_file(Path(project_path))
    if not pf.is_file():
        return None
    data = read_json(pf, default=None)
    if not data:
        return None
    proj = Project.from_dict(data)
    # v1 → v2 defaults are supplied by Project.from_dict (members empty,
    # goal/repo None, schema stays 1). Callers that want a save to upgrade
    # just call save_project.
    proj.path = proj.path or str(Path(project_path).resolve())
    return proj


def save_project(project: Project) -> Path:
    """Serialize a project to disk, bumping the schema to current."""
    project.schema = SCHEMA_VERSION
    pf = _project_file(Path(project.path))
    pf.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(pf, json.dumps(project.to_dict(), indent=2) + "\n")
    return pf


def _ensure_scaffold(p: Path) -> None:
    for sub in (
        ".metasphere",
        ".tasks/active",
        ".tasks/completed",
        ".messages/inbox",
        ".messages/outbox",
        ".changelog",
        ".learnings",
    ):
        (p / sub).mkdir(parents=True, exist_ok=True)


def _register(paths: Paths, project: Project) -> None:
    registry = _load_registry(paths)
    if not any(entry.get("path") == project.path for entry in registry):
        registry.append(
            {"name": project.name, "path": project.path, "registered": _now_iso()}
        )
        write_json(_projects_file(paths), registry)


# ---------------------------------------------------------------------------
# init_project (legacy minimal constructor, signature preserved)
# ---------------------------------------------------------------------------


def init_project(
    name: Optional[str] = None,
    path: Optional[Path] = None,
    *,
    goal: Optional[str] = None,
    repo: Optional[str] = None,
    members: Optional[list[dict]] = None,
    paths: Optional[Paths] = None,
) -> Project:
    """Create (or re-read) a project and register it. Backward-compatible
    with the v1 signature — all new kwargs default to None/empty."""
    paths = paths or resolve()
    p = Path(path).resolve() if path else Path.cwd().resolve()
    name = name or p.name

    _ensure_scaffold(p)

    # Prefer updating an existing on-disk project rather than clobbering.
    existing = load_project(p)
    if existing is not None:
        if goal and not existing.goal:
            existing.goal = goal
        if repo and not existing.repo:
            existing.repo = {"url": repo, "default_branch": "main",
                             "managed_by_metasphere": True}
        if members:
            for md in members:
                if not any(m.id == md.get("id") for m in existing.members):
                    existing.members.append(Member.from_dict(md))
        save_project(existing)
        _register(paths, existing)
        return existing

    proj = Project(
        name=name,
        path=str(p),
        created=_now_iso(),
        status="active",
        goal=goal,
        repo=({"url": repo, "default_branch": "main",
               "managed_by_metasphere": True} if repo else None),
        members=[Member.from_dict(m) for m in (members or [])],
    )
    save_project(proj)
    _register(paths, proj)
    return proj


# ---------------------------------------------------------------------------
# new_project — rich constructor with --repo auto-clone
# ---------------------------------------------------------------------------


def _clone_repo(url: str, dest: Path) -> None:
    print(f"cloning {url} → {dest}")
    subprocess.run(
        ["git", "clone", url, str(dest)],
        check=True,
    )


def new_project(
    name: str,
    *,
    path: Optional[Path] = None,
    goal: Optional[str] = None,
    repo: Optional[str] = None,
    members: Optional[list[dict]] = None,
    paths: Optional[Paths] = None,
    git_clone: Any = None,  # injection point for tests
) -> Project:
    """Rich constructor (backs ``metasphere project new``).

    Behavior:
      * If ``path`` is omitted, defaults to ``CWD/<name>``.
      * If ``repo`` is given and ``path`` does not exist, auto-clone.
      * If ``repo`` is given and ``path`` exists AND is a non-empty
        directory, raise ``FileExistsError`` (per the agreed default).
      * ``members`` is a list of dicts ``{id, role, persistent}`` —
        persistent members get a stub MISSION.md auto-written if one
        doesn't already exist.
    """
    paths = paths or resolve()
    p = Path(path).resolve() if path else (Path.cwd() / name).resolve()

    cloner = git_clone or _clone_repo
    if repo:
        if p.exists() and p.is_dir() and any(p.iterdir()):
            raise FileExistsError(
                f"{p} already exists and is non-empty; remove --path or omit --repo"
            )
        p.parent.mkdir(parents=True, exist_ok=True)
        cloner(repo, p)

    _ensure_scaffold(p)
    proj = Project(
        name=name,
        path=str(p),
        created=_now_iso(),
        status="active",
        goal=goal,
        repo=({"url": repo, "default_branch": "main",
               "managed_by_metasphere": True} if repo else None),
        members=[Member.from_dict(m) for m in (members or [])],
    )

    # Optional telegram forum topic auto-creation. If the forum isn't
    # configured (or topic creation fails), warn loudly so the operator
    # knows what to fix; the project is still created without a topic
    # and can be retro-attached later via ``project topic create``.
    try:
        from .telegram import groups as tg_groups
        forum_id = tg_groups.get_forum_id(paths)
        if not forum_id:
            logger.warning(
                "project %r: no telegram forum configured "
                "(missing %s); skipping topic auto-creation. "
                "Run `metasphere telegram groups setup` then "
                "`metasphere project topic create %s` to attach.",
                name, paths.config / "telegram_forum_id", name,
            )
        else:
            topic = tg_groups.create_topic(name, paths=paths)
            proj.telegram_topic = {"id": topic.id, "name": topic.name}
    except Exception as e:
        logger.warning(
            "project %r: telegram topic auto-creation failed: %s. "
            "Run `metasphere project topic create %s` to retry.",
            name, e, name,
        )

    save_project(proj)
    _register(paths, proj)

    # Auto-write stub MISSION.md for persistent members.
    for m in proj.members:
        if m.persistent:
            _ensure_stub_mission(m.id, proj, paths=paths)

    return proj


# ---------------------------------------------------------------------------
# Retro-attach a telegram forum topic to an existing project
# ---------------------------------------------------------------------------


def attach_topic(name_or_path: str | Path, *,
                 paths: Optional[Paths] = None) -> Project:
    """Idempotently attach a telegram forum topic to an existing project.

    If the project already has ``telegram_topic`` set, this is a no-op
    (returns the project unchanged). Otherwise creates a forum topic
    via the bot API, writes ``telegram_topic`` into ``project.json``,
    and returns the updated project.
    """
    paths = paths or resolve()
    proj = _require(name_or_path, paths)
    if proj.telegram_topic:
        return proj
    from .telegram import groups as tg_groups
    forum_id = tg_groups.get_forum_id(paths)
    if not forum_id:
        raise RuntimeError(
            f"telegram forum not configured "
            f"(missing {paths.config / 'telegram_forum_id'}); "
            f"run `metasphere telegram groups setup` first"
        )
    topic = tg_groups.create_topic(proj.name, paths=paths)
    proj.telegram_topic = {"id": topic.id, "name": topic.name}
    save_project(proj)
    return proj


# ---------------------------------------------------------------------------
# Message mirroring into project telegram topic
# ---------------------------------------------------------------------------


def mirror_message_to_project_topic(scope: Path, label: str, body: str,
                                    from_agent: str, *,
                                    paths: Optional[Paths] = None) -> Optional[int]:
    """If ``scope`` is inside a project that has a telegram_topic set,
    mirror the message to that topic. Returns the topic id on success,
    None otherwise. Additive: the caller still writes to .messages/ as
    normal; this just echoes to the group.
    """
    paths = paths or resolve()
    proj = project_for_scope(Path(scope), paths=paths)
    if proj is None or not proj.telegram_topic:
        return None
    try:
        from .telegram import groups as tg_groups
        tg_groups.send_to_topic(
            int(proj.telegram_topic["id"]),
            f"{label} {body}",
            agent=from_agent,
            paths=paths,
        )
        return int(proj.telegram_topic["id"])
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Member API
# ---------------------------------------------------------------------------


def _ensure_stub_mission(agent_id: str, project: Project,
                         *, paths: Paths) -> None:
    if not agent_id.startswith("@"):
        agent_id = "@" + agent_id

    # Find the member to check for a spec
    member = None
    for m in project.members:
        if m.id == agent_id:
            member = m
            break

    # If the member has a spec, use full persona seeding
    if member and member.spec:
        from .specs import get_spec, seed_agent
        spec = get_spec(member.spec, paths=paths)
        if spec:
            seed_agent(
                agent_id, spec,
                project_name=project.name,
                project_goal=project.goal or "",
                scope=project.path,
                paths=paths,
            )
            return
        logger.warning(
            "Spec '%s' not found for %s; falling back to stub MISSION.md",
            member.spec, agent_id,
        )

    # Fallback: stub MISSION.md only (legacy behavior)
    agent_dir = paths.agent_dir(agent_id)
    agent_dir.mkdir(parents=True, exist_ok=True)
    mission = agent_dir / "MISSION.md"
    if mission.is_file():
        return
    role = member.role if member else "contributor"
    body = (
        f"# Mission: {agent_id}\n\n"
        f"Project: **{project.name}**\n"
        f"Role: {role}\n\n"
        f"## Goal\n\n{project.goal or '(no goal set)'}\n\n"
        f"## Notes\n\n"
        f"Auto-generated stub — edit this file to flesh out your mission.\n"
        f"`metasphere agent wake {agent_id}` will honour MISSION.md's presence\n"
        f"as the persistence marker.\n"
    )
    atomic_write_text(mission, body)


def get_project(name_or_path: str | Path,
                *, paths: Optional[Paths] = None) -> Optional[Project]:
    """Look up a project by name (registry) or by path."""
    paths = paths or resolve()
    if isinstance(name_or_path, Path) or (
        isinstance(name_or_path, str) and ("/" in name_or_path or name_or_path.startswith("."))
    ):
        return load_project(Path(name_or_path))
    for entry in _load_registry(paths):
        if entry.get("name") == name_or_path:
            return load_project(Path(entry["path"]))
    return None


def _require(name_or_path: str | Path, paths: Paths) -> Project:
    proj = get_project(name_or_path, paths=paths)
    if proj is None:
        raise FileNotFoundError(f"project not found: {name_or_path}")
    return proj


def add_member(name_or_path: str | Path, agent_id: str, *,
               role: str = "contributor", persistent: bool = False,
               paths: Optional[Paths] = None) -> Project:
    paths = paths or resolve()
    proj = _require(name_or_path, paths)
    if not agent_id.startswith("@"):
        agent_id = "@" + agent_id
    # Dedup by id — last write wins on role/persistent.
    proj.members = [m for m in proj.members if m.id != agent_id]
    proj.members.append(Member(id=agent_id, role=role, persistent=persistent))
    save_project(proj)
    if persistent:
        _ensure_stub_mission(agent_id, proj, paths=paths)
    return proj


def remove_member(name_or_path: str | Path, agent_id: str, *,
                  paths: Optional[Paths] = None) -> Project:
    paths = paths or resolve()
    proj = _require(name_or_path, paths)
    if not agent_id.startswith("@"):
        agent_id = "@" + agent_id
    proj.members = [m for m in proj.members if m.id != agent_id]
    save_project(proj)
    return proj


def list_members(name_or_path: str | Path,
                 *, paths: Optional[Paths] = None) -> list[Member]:
    paths = paths or resolve()
    proj = _require(name_or_path, paths)
    return list(proj.members)


def wake_members(name_or_path: str | Path, *,
                 paths: Optional[Paths] = None,
                 waker: Any = None) -> list[str]:
    """Wake every persistent member of a project. Returns the agent ids
    that were waked. ``waker`` is an injection hook (tests mock tmux).
    """
    paths = paths or resolve()
    proj = _require(name_or_path, paths)
    from . import agents as _agents

    wake_fn = waker or _agents.wake_persistent
    waked: list[str] = []
    for m in proj.members:
        if not m.persistent:
            continue
        try:
            wake_fn(m.id, paths=paths)
            waked.append(m.id)
        except Exception:
            continue

    # Telegram announcement if the project has a topic.
    if proj.telegram_topic and waked:
        try:
            from .telegram import groups as tg_groups
            tg_groups.send_to_topic(
                int(proj.telegram_topic["id"]),
                f"project waking: members {', '.join(waked)}, "
                f"goal {proj.goal or '(none)'}",
                agent="@orchestrator",
                paths=paths,
            )
        except Exception:
            pass

    return waked


# ---------------------------------------------------------------------------
# project_for_scope
# ---------------------------------------------------------------------------


def project_for_scope(scope: Path,
                      paths: Optional[Paths] = None) -> Optional[Project]:
    """Walk upward from ``scope`` looking for ``.metasphere/project.json``."""
    scope = Path(scope).resolve()
    cur = scope
    while True:
        if (cur / ".metasphere" / "project.json").is_file():
            return load_project(cur)
        if cur.parent == cur:
            return None
        cur = cur.parent


# ---------------------------------------------------------------------------
# List
# ---------------------------------------------------------------------------


def list_projects(*, paths: Optional[Paths] = None) -> list[Project]:
    paths = paths or resolve()
    out: list[Project] = []
    for entry in _load_registry(paths):
        name = entry.get("name", "")
        ep = Path(entry.get("path", ""))
        # Try loading from the repo directory first, then from
        # ~/.metasphere/projects/<name>/ (the canonical location for
        # projects created via `metasphere project new`).
        proj = load_project(ep)
        if proj is None:
            # Projects created via `metasphere project new` store their
            # project.json directly at ~/.metasphere/projects/<name>/project.json
            # (not under a .metasphere/ subdirectory). Try loading directly.
            meta_proj_file = paths.projects / name / "project.json"
            if meta_proj_file.is_file():
                data = read_json(meta_proj_file, default=None)
                if data:
                    proj = Project.from_dict(data)
                    proj.path = proj.path or str(ep)
        if proj is None:
            out.append(Project(
                name=name,
                path=entry.get("path", ""),
                status="missing",
            ))
        else:
            out.append(proj)
    return out


# ---------------------------------------------------------------------------
# Changelog / learnings (preserved from v1)
# ---------------------------------------------------------------------------


def _find_project(name_or_none: Optional[str], paths: Paths) -> Optional[Path]:
    if name_or_none:
        for entry in _load_registry(paths):
            if entry.get("name") == name_or_none:
                return Path(entry["path"])
        return None
    cur = Path.cwd().resolve()
    for ancestor in [cur, *cur.parents]:
        if (ancestor / ".metasphere").is_dir():
            return ancestor
    return None


def project_changelog(name: Optional[str] = None, *, since: str = "1 day ago",
                      paths: Optional[Paths] = None) -> Path:
    paths = paths or resolve()
    proj = _find_project(name, paths)
    if proj is None:
        raise FileNotFoundError("project not found")
    today = _dt.date.today().isoformat()
    out_file = proj / ".changelog" / f"{today}.md"

    lines: list[str] = [f"# Changelog - {today}", "", f"Project: {proj.name}", ""]

    if (proj / ".git").exists():
        lines += ["## Commits", ""]
        try:
            log = subprocess.run(
                ["git", "-C", str(proj), "log", "--oneline", f"--since={since}"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                text=True, check=False,
            ).stdout.strip().splitlines()
            for ln in log[:20]:
                lines.append(f"- {ln}")
        except FileNotFoundError:
            pass
        lines.append("")

    lines += ["## Tasks Completed", ""]
    completed_dir = proj / ".tasks" / "completed"
    seen_titles: set[str] = set()
    if completed_dir.is_dir():
        for tf in sorted(completed_dir.glob("*.task")):
            title = ""
            try:
                for raw in tf.read_text(errors="replace").splitlines():
                    if raw.lower().startswith("title:"):
                        title = raw.split(":", 1)[1].strip()
                        break
                if not title:
                    title = tf.stem
            except OSError:
                title = tf.stem
            if title and title not in seen_titles:
                seen_titles.add(title)
                lines.append(f"- {title}")
    events_log = paths.events_log
    if events_log.exists():
        for raw in events_log.read_text(errors="replace").splitlines()[-2000:]:
            try:
                e = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if e.get("type") == "task.complete":
                scope = (e.get("meta") or {}).get("scope", "")
                if isinstance(scope, str) and scope.startswith(str(proj)):
                    msg = e.get("message", "")
                    if msg and msg not in seen_titles:
                        seen_titles.add(msg)
                        lines.append(f"- {msg}")
    lines.append("")

    lines += ["## Agent Activity", ""]
    agents_dir = paths.agents
    if agents_dir.exists():
        for ad in sorted(agents_dir.iterdir()):
            if not ad.is_dir() or not ad.name.startswith("@"):
                continue
            scope_path = (ad / "scope")
            scope = scope_path.read_text().strip() if scope_path.exists() else ""
            if scope.startswith(str(proj)):
                status_path = ad / "status"
                status = status_path.read_text().strip() if status_path.exists() else "?"
                lines.append(f"- {ad.name}: {status}")
    lines.append("")

    atomic_write_text(out_file, "\n".join(lines))
    return out_file


def project_learnings(name: Optional[str] = None, *,
                      paths: Optional[Paths] = None) -> Path:
    paths = paths or resolve()
    proj = _find_project(name, paths)
    if proj is None:
        raise FileNotFoundError("project not found")
    today = _dt.date.today().isoformat()
    out_file = proj / ".learnings" / f"aggregated-{today}.md"

    lines: list[str] = [
        f"# Learnings - {proj.name}",
        f"Generated: {_now_iso()}",
        "",
    ]
    agents_dir = paths.agents
    if agents_dir.exists():
        for ad in sorted(agents_dir.iterdir()):
            if not ad.is_dir() or not ad.name.startswith("@"):
                continue
            scope_path = ad / "scope"
            scope = scope_path.read_text().strip() if scope_path.exists() else ""
            learnings_dir = ad / "learnings"
            if not (scope.startswith(str(proj)) and learnings_dir.is_dir()):
                continue
            agent_files = sorted(learnings_dir.glob("*.md"))
            if not agent_files:
                continue
            lines += [f"## {ad.name}", ""]
            for f in agent_files:
                lines.append(f"### {f.stem}")
                lines.append("")
                lines.append(f.read_text(errors="replace").rstrip())
                lines.append("")

    proj_learnings = proj / ".learnings"
    if proj_learnings.is_dir():
        proj_files = [f for f in sorted(proj_learnings.glob("*.md"))
                      if "aggregated" not in f.name]
        if proj_files:
            lines += ["## Project-level", ""]
            for f in proj_files:
                lines.append(f"### {f.stem}")
                lines.append(f.read_text(errors="replace").rstrip())
                lines.append("")

    atomic_write_text(out_file, "\n".join(lines))
    return out_file
