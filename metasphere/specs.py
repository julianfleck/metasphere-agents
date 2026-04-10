"""Agent spec loading and persona seeding.

Specs are YAML files in ``specs/`` (repo-level) or ``~/.metasphere/specs/``
(user-level). Each spec defines a reusable agent role: soul, mission,
sandbox level, and triggers.

Seeding a spec into an agent directory writes the full persona stack
(SOUL.md, MISSION.md, persona-index.md) so the agent wakes with voice
and purpose — not a blank-slate Claude.
"""

from __future__ import annotations

import datetime as _dt
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .io import atomic_write_text
from .paths import Paths, resolve

logger = logging.getLogger(__name__)


@dataclass
class AgentSpec:
    """Parsed agent spec."""
    name: str
    role: str
    description: str
    sandbox: str = "none"
    soul: str = ""
    mission: str = ""
    triggers: list[dict] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict) -> "AgentSpec":
        return cls(
            name=str(d.get("name", "")),
            role=str(d.get("role", "contributor")),
            description=str(d.get("description", "")),
            sandbox=str(d.get("sandbox", "none")),
            soul=str(d.get("soul", "")).strip(),
            mission=str(d.get("mission", "")).strip(),
            triggers=list(d.get("triggers", [])),
        )


def _yaml_load(path: Path) -> dict:
    """Load YAML without requiring PyYAML — fall back to simple parser."""
    try:
        import yaml
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except ImportError:
        # Minimal YAML-subset parser for our spec format.
        # Handles top-level keys, multiline strings (|), and list items.
        return _simple_yaml_parse(path.read_text(encoding="utf-8"))


def _simple_yaml_parse(text: str) -> dict:
    """Parse the subset of YAML our specs use. Not a full parser."""
    result: dict = {}
    current_key: str = ""
    current_block: list[str] = []
    in_block = False
    in_list = False
    current_list: list[dict] = []

    for line in text.split("\n"):
        stripped = line.strip()

        # Block scalar continuation
        if in_block:
            if line and not line[0].isspace() and not line.startswith(" "):
                # End of block
                result[current_key] = "\n".join(current_block)
                in_block = False
                # Fall through to parse this line as a new key
            else:
                # Strip common leading indent (2 spaces)
                if line.startswith("  "):
                    current_block.append(line[2:])
                else:
                    current_block.append(line)
                continue

        # List continuation
        if in_list:
            if stripped.startswith("- "):
                item_str = stripped[2:]
                item: dict = {}
                for part in item_str.split(","):
                    part = part.strip()
                    if ":" in part:
                        k, v = part.split(":", 1)
                        item[k.strip()] = v.strip()
                current_list.append(item)
                continue
            else:
                result[current_key] = current_list
                in_list = False
                current_list = []

        # Top-level key
        if ":" in stripped and not stripped.startswith("-") and not stripped.startswith("#"):
            key, val = stripped.split(":", 1)
            key = key.strip()
            val = val.strip()

            if val == "|":
                # Start block scalar
                current_key = key
                current_block = []
                in_block = True
            elif val == "":
                # Could be start of a list
                current_key = key
                in_list = False
                # Peek: handled on next iteration
            elif val.startswith("["):
                # Inline list
                result[key] = val
            else:
                result[key] = val

        elif stripped.startswith("- ") and current_key:
            # Start of list for current_key
            in_list = True
            item_str = stripped[2:]
            item = {}
            for part in item_str.split(","):
                part = part.strip()
                if ":" in part:
                    k, v = part.split(":", 1)
                    item[k.strip()] = v.strip()
            current_list = [item]

    # Flush any pending block/list
    if in_block:
        result[current_key] = "\n".join(current_block)
    if in_list and current_list:
        result[current_key] = current_list

    return result


# ---------------------------------------------------------------------------
# Spec discovery
# ---------------------------------------------------------------------------

def _spec_dirs(paths: Paths | None = None) -> list[Path]:
    """Return directories to search for spec YAML files, in priority order."""
    paths = paths or resolve()
    dirs = []
    # User-level specs (~/.metasphere/specs/)
    user_specs = paths.root / "specs"
    if user_specs.is_dir():
        dirs.append(user_specs)
    # Repo-level specs (from METASPHERE_REPO_ROOT / specs/)
    repo_specs = paths.repo / "specs"
    if repo_specs.is_dir() and repo_specs.resolve() not in [d.resolve() for d in dirs]:
        dirs.append(repo_specs)
    # Package-level specs (specs/ next to metasphere/ package)
    pkg_specs = Path(__file__).resolve().parent.parent / "specs"
    if pkg_specs.is_dir() and pkg_specs.resolve() not in [d.resolve() for d in dirs]:
        dirs.append(pkg_specs)
    return dirs


def list_specs(paths: Paths | None = None) -> list[AgentSpec]:
    """List all available agent specs."""
    specs = {}
    for d in reversed(_spec_dirs(paths)):  # repo first, user overrides
        for f in sorted(d.glob("*.yaml")) + sorted(d.glob("*.yml")):
            try:
                data = _yaml_load(f)
                spec = AgentSpec.from_dict(data)
                if spec.name:
                    specs[spec.name] = spec
            except Exception as e:
                logger.warning("Failed to load spec %s: %s", f, e)
    return list(specs.values())


def get_spec(name: str, paths: Paths | None = None) -> Optional[AgentSpec]:
    """Load a spec by name."""
    for d in _spec_dirs(paths):
        for ext in ("yaml", "yml"):
            f = d / f"{name}.{ext}"
            if f.is_file():
                try:
                    data = _yaml_load(f)
                    return AgentSpec.from_dict(data)
                except Exception as e:
                    logger.warning("Failed to load spec %s: %s", f, e)
    return None


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
    """Seed a full persona stack from a spec into an agent directory.

    Creates: SOUL.md, MISSION.md, persona-index.md, scope, status.
    Idempotent unless ``force=True`` — won't overwrite existing files.

    Returns the agent directory path.
    """
    paths = paths or resolve()
    if not agent_id.startswith("@"):
        agent_id = "@" + agent_id

    agent_dir = paths.agent_dir(agent_id)
    agent_dir.mkdir(parents=True, exist_ok=True)

    # --- SOUL.md ---
    soul_path = agent_dir / "SOUL.md"
    if force or not soul_path.is_file():
        soul_content = f"# {agent_id}\n\n"
        if spec.description:
            soul_content += f"_{spec.description}_\n\n"
        soul_content += f"Role: {spec.role}\n"
        if spec.sandbox != "none":
            soul_content += f"Sandbox: {spec.sandbox}\n"
        soul_content += f"\n---\n\n{spec.soul}\n"
        atomic_write_text(soul_path, soul_content)
        logger.info("Seeded %s/SOUL.md from spec '%s'", agent_id, spec.name)

    # --- MISSION.md ---
    mission_path = agent_dir / "MISSION.md"
    if force or not mission_path.is_file():
        mission_content = f"# Mission: {agent_id}\n\n"
        if project_name:
            mission_content += f"Project: **{project_name}**\n"
        mission_content += f"Role: {spec.role}\n"
        mission_content += f"Spec: {spec.name}\n\n"
        mission_content += f"## Goal\n\n{spec.mission}\n"
        if project_goal:
            mission_content += f"\n## Project Goal\n\n{project_goal}\n"
        atomic_write_text(mission_path, mission_content)
        logger.info("Seeded %s/MISSION.md from spec '%s'", agent_id, spec.name)

    # --- persona-index.md ---
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

    # --- spec reference (for later introspection) ---
    spec_ref = agent_dir / "spec"
    atomic_write_text(spec_ref, spec.name)

    return agent_dir
