"""Central agent-team configuration.

Loads ``~/.metasphere/teams.yaml`` — a flat agent→projects roster —
and exposes lookup helpers for the project-capsule resolution chain.

Schema (see ``templates/install/teams.yaml`` for the canonical
example):

.. code-block:: yaml

    agents:
      alpha-eng:
        projects: [alpha]
      orchestrator:
        projects: [alpha, beta, metasphere-agents]

Resolution context: the per-turn project capsule
(:func:`metasphere.context._render_project_capsule`) consults this
config AFTER MISSION.md frontmatter (the explicit override) and
BEFORE path-nested inference (the dir-layout fallback). The lookup
covers agents whose name doesn't follow the ``<project>-<role>``
convention — e.g. ``@spot`` — which previously could not be
resolved without frontmatter under B4's name-prefix string-match.

Failures are silent: missing / malformed / schemaless files yield an
empty config so the resolution chain falls through to path-nested
inference rather than crashing the per-turn context build."""

from __future__ import annotations

from pathlib import Path

import yaml

from .paths import Paths


_TEAMS_FILENAME = "teams.yaml"

# Cache the parsed config per filesystem path so we don't reparse on
# every turn. The cache invalidates on mtime change so operator edits
# land within one tick of the next read.
_CACHE: dict[Path, tuple[float, dict[str, list[str]]]] = {}


def _load_teams_config(paths: Paths) -> dict[str, list[str]]:
    """Read ``paths.root / teams.yaml`` into ``{agent_name: [project, ...]}``.

    Returns ``{}`` for any failure mode: missing file, unreadable
    file, malformed YAML, schema mismatch. Lookups against an empty
    config naturally return ``[]``, so the project-capsule resolution
    chain degrades into path-nested inference rather than blowing up.

    Cached per absolute path + mtime so per-turn calls are O(1) once
    warm; operator edits to ``teams.yaml`` take effect on the next
    turn after save."""
    config_path = paths.root / _TEAMS_FILENAME
    try:
        mtime = config_path.stat().st_mtime
    except OSError:
        return {}

    cached = _CACHE.get(config_path)
    if cached is not None and cached[0] == mtime:
        return cached[1]

    try:
        raw = config_path.read_text(encoding="utf-8")
    except OSError:
        return {}

    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError:
        return {}

    if not isinstance(data, dict):
        return {}

    agents = data.get("agents")
    if not isinstance(agents, dict):
        return {}

    out: dict[str, list[str]] = {}
    for name, entry in agents.items():
        if not isinstance(name, str) or not name:
            continue
        if not isinstance(entry, dict):
            out[name] = []
            continue
        projects = entry.get("projects")
        if isinstance(projects, list):
            out[name] = [p for p in projects if isinstance(p, str) and p]
        elif isinstance(projects, str) and projects:
            out[name] = [projects]
        else:
            out[name] = []

    _CACHE[config_path] = (mtime, out)
    return out


def _lookup_agent_projects(agent: str, paths: Paths) -> list[str]:
    """Return the project list registered for ``agent`` in teams.yaml.

    Accepts identifiers with or without the leading ``@``. Returns
    ``[]`` when the agent is unregistered, when teams.yaml is missing,
    or when the agent's entry has no projects key. Callers treat the
    empty list as "fall through to the next resolution step"."""
    if not agent:
        return []
    name = agent.lstrip("@")
    if not name:
        return []
    config = _load_teams_config(paths)
    return list(config.get(name, []))
