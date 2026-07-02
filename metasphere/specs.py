"""Agent spec loading and persona seeding.

Specs are directories under ``templates/agents/<role>/`` in the repo,
with an optional user override at ``~/.metasphere/templates/agents/``
(canonical) or the legacy ``~/.metasphere/specs/`` (still searched
for back-compat; deprecated). Each role directory holds:

    templates/agents/critic/
      config.md     — metadata frontmatter (name, role, sandbox, triggers)
      SOUL.md       — personality, voice, operating rules
      MISSION.md    — default mission template (with {{variables}})
      AGENTS.md     — runtime guidelines

Seeding copies these files into ``~/.metasphere/agents/@name/`` with
variable substitution, so the agent wakes with voice and purpose.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .agents import _validate_agent_name
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
    auto_memory: bool = True
    surfaces: list[str] = field(default_factory=list)
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
            auto_memory=str(frontmatter.get("auto_memory", "true")).lower() == "true",
            surfaces=_coerce_surfaces(frontmatter.get("surfaces", [])),
            spec_dir=spec_dir,
        )


def _coerce_surfaces(raw) -> list[str]:
    """Normalize the frontmatter ``surfaces`` field into ``list[str]``.

    The inline-list parser already returns ``list[str]`` for the
    ``[a, b]`` form; this helper also accepts the missing-field empty
    default and a scalar ``surfaces: telegram`` form (treated as a
    single-entry list) so operators can write whichever reads better
    in their config.md.
    """
    if not raw:
        return []
    if isinstance(raw, list):
        return [str(s).strip() for s in raw if str(s).strip()]
    s = str(raw).strip()
    return [s] if s else []


def _parse_frontmatter(text: str) -> dict:
    """Extract YAML-style frontmatter from a markdown file (--- delimited).

    Scalar values are returned as ``str``. Inline-list values of the
    form ``key: [a, b, c]`` are returned as ``list[str]`` with each
    element stripped of surrounding whitespace and optional matching
    quotes; empty entries are dropped, so ``[]`` yields ``[]``.

    Dependency-free by design — PyYAML would be the only third-party
    import in this module. Block-style ``- item`` lists are NOT
    supported (yagni until a frontmatter field needs them).
    """
    result: dict = {}
    lines = text.split("\n")
    if not lines or lines[0].strip() != "---":
        return result
    for line in lines[1:]:
        if line.strip() == "---":
            break
        if ":" not in line:
            continue
        key, val = line.split(":", 1)
        key = key.strip()
        val = val.strip()
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            if not inner:
                result[key] = []
                continue
            items: list[str] = []
            for raw in inner.split(","):
                item = raw.strip()
                if len(item) >= 2 and item[0] == item[-1] and item[0] in ("'", '"'):
                    item = item[1:-1]
                if item:
                    items.append(item)
            result[key] = items
        else:
            result[key] = val
    return result


# ---------------------------------------------------------------------------
# Spec discovery
# ---------------------------------------------------------------------------

def _spec_dirs(paths: Paths | None = None) -> list[Path]:
    """Return directories to search for role subdirectories.

    Search order, first-match-wins per role name:

    1. ``~/.metasphere/templates/agents/`` — canonical user override
    2. ``~/.metasphere/specs/`` — legacy user override (deprecated; kept
       so existing operator customizations don't silently stop working)
    3. ``$METASPHERE_PROJECT_ROOT/templates/agents/`` — repo-local
    4. package-relative ``templates/agents/`` — shipped defaults
    """
    paths = paths or resolve()
    dirs = []
    seen: set[str] = set()

    for candidate in [
        paths.root / "templates" / "agents",
        paths.root / "specs",  # deprecated, still honored
        paths.project_root / "templates" / "agents",
        Path(__file__).resolve().parent.parent / "templates" / "agents",
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


_LEGACY_SPEC_RENAMES = {
    "implementer": "eng",
    "planner": "lead",
    "reviewer": "critic",
    "monitor": "explorer",
}


def get_spec(name: str, paths: Paths | None = None) -> Optional[AgentSpec]:
    """Load a spec by name (searches all spec directories).

    Emits a one-line logger hint when an operator passes a legacy spec
    name that was renamed in the templates/agents/ collapse. The hint
    helps shell aliases / scripts catch up without keeping the alias
    map alive in resolution itself.
    """
    for parent in _spec_dirs(paths):
        d = parent / name
        if d.is_dir():
            spec = AgentSpec.from_dir(d)
            if spec:
                return spec
    if name in _LEGACY_SPEC_RENAMES:
        new_name = _LEGACY_SPEC_RENAMES[name]
        logger.warning(
            "Spec '%s' was renamed to '%s' in the templates/agents/ "
            "collapse — retry with --spec %s.",
            name, new_name, new_name,
        )
    return None


def get_spec_for_agent(agent_id: str, paths: Paths | None = None) -> Optional[AgentSpec]:
    """Load the spec referenced by a seeded agent's ``spec`` pointer.

    Returns ``None`` if the agent dir or its ``spec`` file is missing,
    or if no matching spec name resolves — callers default to the
    spec-absent behavior (e.g., emit memory section).
    """
    paths = paths or resolve()
    agent_dir = paths.find_agent_dir(agent_id)
    if agent_dir is None:
        return None
    spec_pointer = agent_dir / "spec"
    if not spec_pointer.is_file():
        return None
    try:
        spec_name = spec_pointer.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not spec_name:
        return None
    return get_spec(spec_name, paths)


# ---------------------------------------------------------------------------
# Variable substitution
# ---------------------------------------------------------------------------

def _substitute(text: str, variables: dict[str, str]) -> str:
    """Replace ``{{variable}}`` placeholders in text.

    Tolerates both ``{{key}}`` and ``{{ key }}`` (and any whitespace
    around the key) so templates can use whichever style reads better
    in their context. Existing specs use ``{{key}}``; the install/
    projects/ templates use ``{{ key }}`` for human readability.
    """
    pattern = re.compile(r"\{\{\s*(\w+)\s*\}\}")
    return pattern.sub(
        lambda m: variables.get(m.group(1), m.group(0)),
        text,
    )


# ---------------------------------------------------------------------------
# Persona seeding
# ---------------------------------------------------------------------------

def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _find_user_md_template() -> Optional[Path]:
    """Locate the shipped USER.md template for project-level seeding.

    Searches the package-relative ``templates/install/projects/`` dir
    first, then ``$METASPHERE_PROJECT_ROOT/templates/install/projects/``
    if it differs. Returns ``None`` if no template ships.
    """
    candidates = []
    pkg_repo_root = Path(__file__).resolve().parent.parent
    candidates.append(pkg_repo_root / "templates" / "install" / "projects" / "USER.md.template")
    try:
        env_root = Path(resolve().project_root)
        if env_root and env_root != pkg_repo_root:
            candidates.append(env_root / "templates" / "install" / "projects" / "USER.md.template")
    except Exception:
        pass
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def _seed_project_user_md(project_name: str, project_goal: str, paths: Paths) -> Optional[Path]:
    """Ensure ``~/.metasphere/projects/<project>/USER.md`` exists.

    Idempotent: returns the path without rewriting if already present.
    Seeds from the shipped template at
    ``templates/install/projects/USER.md.template`` with placeholder
    substitution if missing. Returns ``None`` if no template ships.
    """
    project_root = paths.root / "projects" / project_name
    user_md = project_root / "USER.md"
    if user_md.is_file():
        return user_md
    template = _find_user_md_template()
    if template is None:
        return None
    project_root.mkdir(parents=True, exist_ok=True)
    variables = {
        "project_name": project_name,
        "project_goal": project_goal or "(no goal set)",
    }
    content = _substitute(template.read_text(encoding="utf-8"), variables)
    atomic_write_text(user_md, content)
    logger.info("Seeded ~/.metasphere/projects/%s/USER.md from template", project_name)
    return user_md


def _link_agent_user_md(agent_dir: Path, project_user_md: Path) -> None:
    """Symlink ``agent_dir/USER.md`` to the project-level USER.md.

    The symlink lets every agent on the project read one source of
    truth — edits to the project USER.md propagate without re-seeding.
    Operators who want a per-agent override can replace the symlink
    with a real file.

    No-op if the agent already has a USER.md (real file or symlink).
    """
    dest = agent_dir / "USER.md"
    if dest.exists() or dest.is_symlink():
        return
    try:
        rel_target = os.path.relpath(project_user_md, agent_dir)
    except ValueError:
        # ``relpath`` raises on cross-drive paths (Windows-only failure
        # mode in practice). Falling back to an absolute symlink target
        # still works; log the unusual case so it's visible if it ever
        # fires on a stranger's install.
        logger.debug(
            "USER.md relpath fell back to absolute (likely cross-drive): "
            "%s -> %s",
            agent_dir, project_user_md,
        )
        rel_target = str(project_user_md)
    try:
        os.symlink(rel_target, dest)
        logger.info("Linked %s/USER.md -> %s", agent_dir.name, rel_target)
    except OSError as e:
        # Visible failure: the agent is left without a USER.md symlink.
        # The seed_agent caller does not abort — better to ship the
        # rest of the persona than fail the spawn — but the log makes
        # the partial state debuggable. Common causes: filesystem
        # without symlink support, dest path under a read-only mount,
        # SELinux/AppArmor denial.
        logger.warning(
            "Failed to symlink USER.md for %s "
            "(dest=%s, target=%s): %s",
            agent_dir.name, dest, rel_target, e,
        )


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

    AGENTS.md is sourced separately from ``templates/agents/<spec.role>/``,
    not from the spec dir — so a spec named ``monitor`` with
    ``role: explorer`` correctly yields monitor's SOUL/MISSION + the
    shared explorer AGENTS.md runtime contract.

    Idempotent unless ``force=True`` — won't overwrite existing files.
    Returns the agent directory path.
    """
    _validate_agent_name(agent_id)
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

    # --- AGENTS.md fallback (role-shared runtime guidelines) ---
    # The persona-copy loop above already lands AGENTS.md when it's
    # present alongside SOUL/MISSION in the spec dir (the canonical
    # post-collapse layout). The fallback below catches the legacy
    # case of a user override under ``~/.metasphere/specs/<custom>/``
    # that ships SOUL/MISSION but not AGENTS.md — those still get the
    # shared role contract from the shipped ``templates/agents/<role>/``.
    agents_md_dest = agent_dir / "AGENTS.md"
    if force or not agents_md_dest.is_file():
        pkg_repo_root = Path(__file__).resolve().parent.parent
        shared = pkg_repo_root / "templates" / "agents" / spec.role / "AGENTS.md"
        if shared.is_file():
            content = _substitute(shared.read_text(encoding="utf-8"), variables)
            atomic_write_text(agents_md_dest, content)
            logger.info("Seeded %s/AGENTS.md from templates/agents/%s/", agent_id, spec.role)

    # --- USER.md (project-level team description, symlinked into agent dir) ---
    # Project-scoped agents share a single USER.md per project at
    # ~/.metasphere/projects/<project>/USER.md. agent_dir/USER.md is a
    # symlink to it so edits propagate without re-seeding. The
    # project-level file gets created from the shipped template on
    # first agent spawn; subsequent spawns reuse it. Root-scope agents
    # (project_name unset) are left to install.sh's heredoc-seed for
    # @orchestrator/USER.md describing the human operator.
    if project_name:
        project_user_md = _seed_project_user_md(project_name, project_goal, paths)
        if project_user_md is not None:
            _link_agent_user_md(agent_dir, project_user_md)

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
