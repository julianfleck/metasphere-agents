"""Tests for B7 — central agent-team config at ~/.metasphere/teams.yaml.

The loader graceful-degrades on every failure mode (missing file,
malformed YAML, wrong schema, missing keys) so the project-capsule
resolution chain falls through to path-nested inference rather than
crashing the per-turn context build.

Operator directive 2026-05-29: replace B4's name-prefix string-match
inference with a config-driven roster.
"""

from __future__ import annotations

from pathlib import Path

from metasphere import teams as _teams
from metasphere.paths import Paths


# ---------------------------------------------------------------------------
# Seed helper
# ---------------------------------------------------------------------------


def _write_teams_yaml(tmp_paths: Paths, content: str) -> Path:
    path = tmp_paths.root / "teams.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def _clear_cache() -> None:
    # The loader caches per-path; clear between test setups that
    # rewrite the same file at the same path without an mtime tick.
    _teams._CACHE.clear()


# ===========================================================================
# Loader
# ===========================================================================


def test_load_teams_config_basic(tmp_paths: Paths):
    _clear_cache()
    _write_teams_yaml(tmp_paths, """
agents:
  widget-eng:
    projects: [widget]
  orchestrator:
    projects: [widget, metasphere-agents, writing]
""")
    out = _teams._load_teams_config(tmp_paths)
    assert out == {
        "widget-eng": ["widget"],
        "orchestrator": ["widget", "metasphere-agents", "writing"],
    }


def test_load_teams_config_missing_file(tmp_paths: Paths):
    _clear_cache()
    # No teams.yaml at all.
    assert _teams._load_teams_config(tmp_paths) == {}


def test_load_teams_config_malformed_yaml(tmp_paths: Paths):
    _clear_cache()
    _write_teams_yaml(tmp_paths, "agents:\n  - this is: not\n   valid: ::\n")
    # Malformed YAML must NOT raise — silent degrade.
    out = _teams._load_teams_config(tmp_paths)
    assert out == {}


def test_load_teams_config_no_agents_key(tmp_paths: Paths):
    _clear_cache()
    _write_teams_yaml(tmp_paths, "unrelated_top_level: 42\n")
    assert _teams._load_teams_config(tmp_paths) == {}


def test_load_teams_config_entry_no_projects_key(tmp_paths: Paths):
    _clear_cache()
    _write_teams_yaml(tmp_paths, """
agents:
  widget-eng:
    something_else: nope
""")
    out = _teams._load_teams_config(tmp_paths)
    assert out == {"widget-eng": []}


def test_load_teams_config_scalar_projects_string(tmp_paths: Paths):
    """Allow scalar projects: <name> as shorthand for projects: [<name>]."""
    _clear_cache()
    _write_teams_yaml(tmp_paths, """
agents:
  spot:
    projects: widget
""")
    out = _teams._load_teams_config(tmp_paths)
    assert out == {"spot": ["widget"]}


def test_load_teams_config_drops_non_string_project_entries(
    tmp_paths: Paths,
):
    _clear_cache()
    _write_teams_yaml(tmp_paths, """
agents:
  widget-eng:
    projects:
      - widget
      - 42
      - null
      - cam
""")
    out = _teams._load_teams_config(tmp_paths)
    assert out == {"widget-eng": ["widget", "cam"]}


def test_load_teams_config_caches_until_mtime_change(tmp_paths: Paths):
    _clear_cache()
    path = _write_teams_yaml(tmp_paths, """
agents:
  widget-eng:
    projects: [widget]
""")
    first = _teams._load_teams_config(tmp_paths)
    assert first == {"widget-eng": ["widget"]}

    # Rewrite content without touching mtime → cached version returns.
    stat = path.stat()
    path.write_text("agents: {}\n", encoding="utf-8")
    import os
    os.utime(path, (stat.st_atime, stat.st_mtime))
    cached = _teams._load_teams_config(tmp_paths)
    assert cached == {"widget-eng": ["widget"]}

    # Bump mtime explicitly forward (filesystem second-precision means
    # os.utime(None) on a freshly-written file can keep the same ts).
    os.utime(path, (stat.st_atime, stat.st_mtime + 5))
    fresh = _teams._load_teams_config(tmp_paths)
    assert fresh == {}


# ===========================================================================
# Lookup
# ===========================================================================


def test_lookup_agent_projects_strips_at_prefix(tmp_paths: Paths):
    _clear_cache()
    _write_teams_yaml(tmp_paths, """
agents:
  widget-eng:
    projects: [widget]
""")
    with_at = _teams._lookup_agent_projects("@widget-eng", tmp_paths)
    without_at = _teams._lookup_agent_projects("widget-eng", tmp_paths)
    assert with_at == ["widget"]
    assert without_at == ["widget"]


def test_lookup_agent_projects_missing_agent_returns_empty(tmp_paths: Paths):
    _clear_cache()
    _write_teams_yaml(tmp_paths, """
agents:
  widget-eng:
    projects: [widget]
""")
    assert _teams._lookup_agent_projects("@spot", tmp_paths) == []


def test_lookup_agent_projects_multi_project(tmp_paths: Paths):
    _clear_cache()
    _write_teams_yaml(tmp_paths, """
agents:
  orchestrator:
    projects: [widget, metasphere-agents, writing, cam]
""")
    out = _teams._lookup_agent_projects("@orchestrator", tmp_paths)
    assert out == ["widget", "metasphere-agents", "writing", "cam"]


def test_lookup_agent_projects_empty_input_returns_empty(tmp_paths: Paths):
    _clear_cache()
    assert _teams._lookup_agent_projects("", tmp_paths) == []
    assert _teams._lookup_agent_projects("@", tmp_paths) == []


def test_lookup_agent_projects_no_teams_yaml(tmp_paths: Paths):
    _clear_cache()
    # No teams.yaml seeded — graceful empty list.
    assert _teams._lookup_agent_projects("@anything", tmp_paths) == []
