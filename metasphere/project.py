"""Project management (port of scripts/metasphere-project).

Projects are directories marked by ``.metasphere/`` containing
``project.json``. The bash version had two known bugs which are
fixed here:

* ``cmd_changelog`` printed to stdout but never wrote the changelog
  file (KNOWN_ISSUES). We now write it.
* ``cmd_learnings``'s ``has_learnings`` flag was inverted, so the
  per-agent header was never emitted. Fixed.
"""

from __future__ import annotations

import datetime as _dt
import json
import subprocess
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from .io import atomic_write_text, read_json, write_json
from .paths import Paths, resolve


@dataclass
class Project:
    name: str
    path: str
    scope: str = ""
    created: str = ""
    status: str = "active"

    def to_dict(self) -> dict:
        return asdict(self)


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _projects_file(paths: Paths) -> Path:
    return paths.root / "projects.json"


def _load_projects(paths: Paths) -> list[dict]:
    return read_json(_projects_file(paths), default=[]) or []


def init_project(name: Optional[str] = None, path: Optional[Path] = None, *,
                 paths: Optional[Paths] = None) -> Project:
    paths = paths or resolve()
    p = Path(path).resolve() if path else Path.cwd().resolve()
    name = name or p.name

    for sub in (".metasphere", ".tasks/active", ".tasks/completed",
                ".messages/inbox", ".messages/outbox",
                ".changelog", ".learnings"):
        (p / sub).mkdir(parents=True, exist_ok=True)

    proj_json = p / ".metasphere" / "project.json"
    if not proj_json.exists():
        atomic_write_text(proj_json, json.dumps({
            "name": name,
            "path": str(p),
            "created": _now_iso(),
            "status": "active",
        }, indent=2) + "\n")

    projects = _load_projects(paths)
    if not any(entry.get("path") == str(p) for entry in projects):
        projects.append({
            "name": name,
            "path": str(p),
            "registered": _now_iso(),
        })
        write_json(_projects_file(paths), projects)

    return Project(name=name, path=str(p), created=_now_iso())


def list_projects(*, paths: Optional[Paths] = None) -> list[Project]:
    paths = paths or resolve()
    out = []
    for entry in _load_projects(paths):
        out.append(Project(
            name=entry.get("name", ""),
            path=entry.get("path", ""),
            status="active" if Path(entry.get("path", "")) .joinpath(".metasphere").is_dir() else "missing",
        ))
    return out


def _find_project(name_or_none: Optional[str], paths: Paths) -> Optional[Path]:
    if name_or_none:
        for entry in _load_projects(paths):
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
    """Generate today's changelog and **write it to disk** (bash bug fix).

    Returns the path to the written changelog file.
    """
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
                    lines.append(f"- {e.get('message', '')}")
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
    """Aggregate per-agent learnings into a single file (fixes inverted flag)."""
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
            # Header emitted exactly once per agent (bash bug fix)
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
