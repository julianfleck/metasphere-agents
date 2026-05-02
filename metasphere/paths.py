"""Single source of truth for metasphere filesystem paths.

Replaces ad-hoc ``${METASPHERE_DIR:-$HOME/.metasphere}`` expansions
scattered across scripts/ (messages, tasks, metasphere-spawn,
metasphere-context, metasphere-schedule, metasphere-telegram, ...).

Resolution rules:
    METASPHERE_DIR          -> ~/.metasphere
    METASPHERE_PROJECT_ROOT -> git toplevel of CWD, else METASPHERE_DIR
    METASPHERE_SCOPE        -> CWD
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from datetime import date
from pathlib import Path


def _env_path(name: str, default: Path) -> Path:
    v = os.environ.get(name)
    return Path(v).expanduser() if v else default


def home() -> Path:
    """Return the metasphere runtime root (``$METASPHERE_DIR`` or ``~/.metasphere``)."""
    return _env_path("METASPHERE_DIR", Path.home() / ".metasphere")


_project_root_cache: dict[tuple[str, str], Path] = {}


def project_root() -> Path:
    """Resolve the project root, caching the git shell-out per (env, cwd) pair.

    Resolution order:
    1. ``METASPHERE_PROJECT_ROOT`` env var (canonical)
    2. ``METASPHERE_REPO_ROOT`` env var (backward compat, no warning)
    3. ``git rev-parse --show-toplevel`` (CLI users in a project dir)
    4. Fall back to ``~/.metasphere`` (METASPHERE_DIR)
    """
    v = os.environ.get("METASPHERE_PROJECT_ROOT") or os.environ.get("METASPHERE_REPO_ROOT")
    if v:
        return Path(v).expanduser()
    cwd = os.getcwd()
    key = ("", cwd)
    cached = _project_root_cache.get(key)
    if cached is not None:
        return cached
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if out:
            result = Path(out)
            _project_root_cache[key] = result
            return result
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    result = home()
    _project_root_cache[key] = result
    return result


# Backward-compat alias — old callers import ``repo_root`` directly.
repo_root = project_root


def scope() -> Path:
    return _env_path("METASPHERE_SCOPE", Path.cwd())


@dataclass(frozen=True)
class Paths:
    """Resolved metasphere paths. Construct fresh if env may have changed."""

    root: Path
    project_root: Path
    scope: Path

    @property
    def repo(self) -> Path:
        """Backward-compat alias for ``project_root``."""
        return self.project_root

    @property
    def agents(self) -> Path:
        return self.root / "agents"

    @property
    def config(self) -> Path:
        return self.root / "config"

    @property
    def state(self) -> Path:
        return self.root / "state"

    @property
    def events(self) -> Path:
        return self.root / "events"

    @property
    def events_log(self) -> Path:
        # Daily rotation: each call recomputes today's path so a long-running
        # process that crosses midnight starts writing to the new dated file
        # without restart. The legacy unrotated ``events.jsonl`` is intentionally
        # left alone — operator-driven migration handles the historical file.
        return self.events / f"events-{date.today():%Y-%m-%d}.jsonl"

    @property
    def schedule(self) -> Path:
        return self.root / "schedule"

    @property
    def schedule_jobs(self) -> Path:
        return self.schedule / "jobs.json"

    @property
    def telegram(self) -> Path:
        return self.root / "telegram"

    @property
    def telegram_stream(self) -> Path:
        return self.telegram / "stream"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def memory(self) -> Path:
        return self.root / "memory"

    @property
    def current_agent_file(self) -> Path:
        return self.root / "current_agent"

    @property
    def projects(self) -> Path:
        return self.root / "projects"

    def agent_dir(self, agent_id: str) -> Path:
        """Global agent directory (system-level agents like @orchestrator)."""
        return self.agents / agent_id

    def project_agents_dir(self, project_name: str) -> Path:
        """Agent directory root for a specific project."""
        return self.projects / project_name / "agents"

    def project_agent_dir(self, project_name: str, agent_id: str) -> Path:
        """Agent identity directory scoped to a project."""
        if not agent_id.startswith("@"):
            agent_id = "@" + agent_id
        return self.project_agents_dir(project_name) / agent_id

    def resolve_agent_dir(self, agent_id: str, project_name: str = "") -> Path:
        """Resolve agent directory: project-scoped if project given, else global."""
        if project_name:
            return self.project_agent_dir(project_name, agent_id)
        return self.agent_dir(agent_id)

    def find_agent_dir(self, agent_id: str) -> Path | None:
        """Discover an agent's identity directory across project-scoped + global.

        Project-scoped first (matches ``metasphere.agents._find_agent_dir``):
        an agent registered under ``~/.metasphere/projects/<proj>/agents/<id>/``
        wins over a same-named entry under ``~/.metasphere/agents/<id>/``.
        Returns ``None`` if no directory exists in either layer — callers
        decide whether to fall back to ``agent_dir(agent_id)`` for write
        targets vs. emit nothing for read-only lookups.
        """
        if not agent_id.startswith("@"):
            agent_id = "@" + agent_id
        if self.projects.is_dir():
            for proj_dir in sorted(self.projects.iterdir()):
                if not proj_dir.is_dir():
                    continue
                candidate = proj_dir / "agents" / agent_id
                if candidate.is_dir():
                    return candidate
        candidate = self.agents / agent_id
        if candidate.is_dir():
            return candidate
        return None

    def messages_dir(self, scope_dir: Path | None = None) -> Path:
        return (scope_dir or self.scope) / ".messages"

    def tasks_dir(self, scope_dir: Path | None = None) -> Path:
        return (scope_dir or self.scope) / ".tasks"


def resolve() -> Paths:
    """Build a Paths bundle from current env / cwd."""
    return Paths(root=home(), project_root=project_root(), scope=scope())


def rel_path(path: Path, project_root: Path) -> str:
    """Render ``path`` as a ``/``-prefixed scope string relative to
    ``project_root``. Falls back to the absolute path string if ``path`` is
    outside ``project_root``. Used everywhere a scope label is printed.
    """
    try:
        rel = Path(path).resolve().relative_to(Path(project_root).resolve())
        s = "/" + str(rel)
    except ValueError:
        s = str(path)
    if s == "/.":
        return "/"
    return s.rstrip("/") or "/"
