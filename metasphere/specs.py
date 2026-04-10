"""Agent spec loading and persona seeding.

Specs are directories in ``specs/`` (repo-level) or ``~/.metasphere/specs/``
(user-level). Each spec directory contains markdown files that define the
agent's persona:

    specs/reviewer/
      SOUL.md       — personality, voice, operating rules
      MISSION.md    — default mission template (with {{variables}})
      config.md     — metadata frontmatter (name, role, sandbox, triggers)

Seeding copies these files into ``~/.metasphere/agents/@name/`` with
variable substitution, so the agent wakes with voice and purpose.
"""

from __future__ import annotations

import datetime as _dt
import logging
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .io import atomic_write_text
from .paths import Paths, resolve

logger = logging.getLogger(__name__)


@dataclass
class AgentSpec:
    """Loaded agent spec."""
    name: str
    role: str
    description: str
    sandbox: str = "none"
    persistent: bool = True
    spec_dir: Path = field(default_factory=lambda: Path("."))

    @classmethod
    def from_dir(cls, spec_dir: Path) -> Optional["AgentSpec"]:
        """Load a spec from a directory containing config.md + persona files."""
        config_path = spec_dir / "config.md"
        if not config_path.is_file():
            return None
        frontmatter = _parse_frontmatter(config_path.read_text(encoding="utf-8"))
        if not frontmatter.get("name"):
            return None
        return cls(
            name=str(frontmatter.get("name", spec_dir.name)),
            role=str(frontmatter.get("role", "contributor")),
            description=str(frontmatter.get("description", "")),
            sandbox=str(frontmatter.get("sandbox", "none")),
            persistent=str(frontmatter.get("persistent", "true")).lower() == "true",
            spec_dir=spec_dir,
        )


def _parse_frontmatter(text: str) -> dict:
    """Extract YAML-style frontmatter from a markdown file (--- delimited)."""
    result = {}
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return result
    for i, line in enumerate(lines[1:], start=1):
        if line.strip() == "---":
            break
        if ":" in line:
            key, val = line.split(":", 1)
            result[key.strip()] = val.strip()
    return result


# ---------------------------------------------------------------------------
# Spec discovery
# ---------------------------------------------------------------------------

def _spec_dirs(paths: Paths | None = None) -> list[Path]:
    """Return directories to search for spec subdirectories."""
    paths = paths or resolve()
    dirs = []
    seen: set[str] = set()

    for candidate in [
        paths.root / "specs",             # ~/.metasphere/specs/
        paths.repo / "specs",             # $METASPHERE_REPO_ROOT/specs/
        Path(__file__).resolve().parent.parent / "specs",  # package-relative
    ]:
        resolved = str(candidate.resolve())
        if candidate.is_dir() and resolved not in seen:
            dirs.append(candidate)
            seen.add(resolved)
    return dirs


def list_specs(paths: Paths | None = None) -> list[AgentSpec]:
    """List all available agent specs."""
    specs: dict[str, AgentSpec] = {}
    for parent in reversed(_spec_dirs(paths)):
        for d in sorted(parent.iterdir()):
            if not d.is_dir() or d.name.startswith("."):
                continue
            spec = AgentSpec.from_dir(d)
            if spec:
                specs[spec.name] = spec
    return list(specs.values())


def get_spec(name: str, paths: Paths | None = None) -> Optional[AgentSpec]:
    """Load a spec by name (searches all spec directories)."""
    for parent in _spec_dirs(paths):
        d = parent / name
        if d.is_dir():
            spec = AgentSpec.from_dir(d)
            if spec:
                return spec
    return None


# ---------------------------------------------------------------------------
# Variable substitution
# ---------------------------------------------------------------------------

def _substitute(text: str, variables: dict[str, str]) -> str:
    """Replace {{variable}} placeholders in text."""
    for key, value in variables.items():
        text = text.replace("{{" + key + "}}", value)
    return text


# ---------------------------------------------------------------------------
# Persona seeding
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def seed_agent(
    agent_id: str,
    spec: AgentSpec,
    *,
    project_name: str = "",
    project_goal: str = "",
    scope: str = "",
    paths: Paths | None = None,
    force: bool = False,
) -> Path:
    """Seed a full persona stack from a spec directory into an agent directory.

    Copies SOUL.md and MISSION.md from the spec, applies variable
    substitution, and generates persona-index.md + LEARNINGS.md.

    Idempotent unless ``force=True`` — won't overwrite existing files.
    Returns the agent directory path.
    """
    paths = paths or resolve()
    if not agent_id.startswith("@"):
        agent_id = "@" + agent_id

    agent_dir = paths.resolve_agent_dir(agent_id, project_name)
    agent_dir.mkdir(parents=True, exist_ok=True)

    # Write project pointer so we can discover which project this agent belongs to
    if project_name:
        atomic_write_text(agent_dir / "project", project_name)

    variables = {
        "agent_id": agent_id,
        "project_name": project_name or "(no project)",
        "project_goal": project_goal or "(no goal set)",
        "scope": scope or str(paths.scope),
        "spec_name": spec.name,
        "role": spec.role,
        "timestamp": _now_iso(),
    }

    # Copy persona files from spec directory with substitution
    for src in spec.spec_dir.iterdir():
        if src.name == "config.md" or src.name.startswith("."):
            continue
        dest = agent_dir / src.name
        if not force and dest.is_file():
            continue
        if src.is_file():
            content = src.read_text(encoding="utf-8")
            content = _substitute(content, variables)
            atomic_write_text(dest, content)
            logger.info("Seeded %s/%s from spec '%s'", agent_id, src.name, spec.name)

    # --- persona-index.md (generated, not from spec) ---
    index_path = agent_dir / "persona-index.md"
    if force or not index_path.is_file():
        index_content = f"# Persona Index: {agent_id}\n\n"
        index_content += "Read SOUL.md and MISSION.md at session start.\n"
        index_content += "Everything else is lazy-loaded.\n\n"
        index_content += "| File | Purpose | Load |\n"
        index_content += "|------|---------|------|\n"
        index_content += "| SOUL.md | Identity, voice, operating rules | Session start |\n"
        index_content += "| MISSION.md | Objectives, project context | Session start |\n"
        index_content += "| HEARTBEAT.md | Current status | On state change |\n"
        index_content += "| LEARNINGS.md | Accumulated insights | After discoveries |\n"
        atomic_write_text(index_path, index_content)

    # --- LEARNINGS.md ---
    learnings_path = agent_dir / "LEARNINGS.md"
    if not learnings_path.is_file():
        atomic_write_text(
            learnings_path,
            f"# Learnings: {agent_id}\n\n"
            f"_Seeded from spec '{spec.name}' on {_now_iso()}_\n\n"
        )

    # --- scope ---
    scope_path = agent_dir / "scope"
    if scope and (force or not scope_path.is_file()):
        atomic_write_text(scope_path, scope)

    # --- status ---
    status_path = agent_dir / "status"
    if not status_path.is_file():
        atomic_write_text(status_path, f"seeded: from spec '{spec.name}'")

    # --- spec reference ---
    atomic_write_text(agent_dir / "spec", spec.name)

    return agent_dir
