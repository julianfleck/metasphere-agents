"""Single source of truth for metasphere filesystem paths.

Replaces ad-hoc ``${METASPHERE_DIR:-$HOME/.metasphere}`` expansions
scattered across scripts/ (messages, tasks, metasphere-spawn,
metasphere-context, metasphere-schedule, metasphere-telegram, ...).

Resolution rules mirror the bash:
    METASPHERE_DIR        -> ~/.metasphere
    METASPHERE_REPO_ROOT  -> git toplevel of CWD, else CWD
    METASPHERE_SCOPE      -> CWD
"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path


def _env_path(name: str, default: Path) -> Path:
    v = os.environ.get(name)
    return Path(v).expanduser() if v else default


def metasphere_dir() -> Path:
    return _env_path("METASPHERE_DIR", Path.home() / ".metasphere")


def repo_root() -> Path:
    v = os.environ.get("METASPHERE_REPO_ROOT")
    if v:
        return Path(v).expanduser()
    try:
        out = subprocess.check_output(
            ["git", "rev-parse", "--show-toplevel"],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
        if out:
            return Path(out)
    except (subprocess.CalledProcessError, FileNotFoundError):
        pass
    return Path.cwd()


def scope() -> Path:
    return _env_path("METASPHERE_SCOPE", Path.cwd())


@dataclass(frozen=True)
class Paths:
    """Resolved metasphere paths. Construct fresh if env may have changed."""

    root: Path
    repo: Path
    scope: Path

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
        return self.events / "events.jsonl"

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

    def agent_dir(self, agent_id: str) -> Path:
        return self.agents / agent_id

    def messages_dir(self, scope_dir: Path | None = None) -> Path:
        return (scope_dir or self.scope) / ".messages"

    def tasks_dir(self, scope_dir: Path | None = None) -> Path:
        return (scope_dir or self.scope) / ".tasks"


def resolve() -> Paths:
    """Build a Paths bundle from current env / cwd."""
    return Paths(root=metasphere_dir(), repo=repo_root(), scope=scope())
