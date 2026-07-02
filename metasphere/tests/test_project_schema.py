"""Schema v2 + v1 fallback tests (layer A)."""

import json
from pathlib import Path

from metasphere.project import (
    Member,
    Project,
    SCHEMA_VERSION,
    init_project,
    load_project,
    save_project,
)


def _v1_project(proj_dir: Path, name: str = "legacy", *, paths=None) -> None:
    """Write a pre-schema-v2 project.json by hand at the canonical location.

    Post-PR #11 there's no in-repo ``.metasphere/project.json`` read
    path — canonical is ``paths.projects/<name>/project.json``. Helper
    seeds both the canonical file and the (empty) in-repo marker dir
    for tests that check "is this a project dir?".
    """
    (proj_dir / ".metasphere").mkdir(parents=True, exist_ok=True)
    # Canonical location. ``load_project`` resolves via
    # registry-reverse-lookup OR basename of project_path — we use
    # ``proj_dir.name`` to match the basename fallback.
    assert paths is not None, "_v1_project needs paths for canonical write"
    cf = paths.projects / proj_dir.name / "project.json"
    cf.parent.mkdir(parents=True, exist_ok=True)
    cf.write_text(
        json.dumps({
            "name": name,
            "path": str(proj_dir),
            "created": "2025-01-01T00:00:00Z",
            "status": "active",
        }) + "\n"
    )


def test_v1_loads_with_empty_members(tmp_paths, tmp_path):
    proj_dir = tmp_path / "legacy"
    proj_dir.mkdir()
    _v1_project(proj_dir, paths=tmp_paths)
    proj = load_project(proj_dir)
    assert proj is not None
    assert proj.name == "legacy"
    assert proj.schema == 1  # untouched on disk
    assert proj.members == []
    assert proj.goal is None
    assert proj.repo is None
    assert proj.links == {}


def test_v1_to_v2_migration_on_save(tmp_paths, tmp_path):
    proj_dir = tmp_path / "legacy"
    proj_dir.mkdir()
    _v1_project(proj_dir, paths=tmp_paths)
    proj = load_project(proj_dir)
    assert proj is not None
    proj.goal = "do the thing"
    save_project(proj)
    # Post-PR #11: canonical location is the only write target.
    canonical = tmp_paths.projects / "legacy" / "project.json"
    raw = json.loads(canonical.read_text())
    assert raw["schema"] == SCHEMA_VERSION
    assert raw["goal"] == "do the thing"
    assert raw["members"] == []


def test_v2_roundtrip(tmp_paths, tmp_path):
    p = tmp_path / "proj"
    p.mkdir()
    proj = Project(
        name="proj",
        path=str(p),
        created="2026-01-01T00:00:00Z",
        goal="ship v1",
        repo={"url": "git@x:y.git", "default_branch": "main",
              "managed_by_metasphere": True},
        members=[Member(id="@lead", role="lead", persistent=True)],
        links={"github_issues": "https://gh/x/y/issues"},
    )
    save_project(proj)
    loaded = load_project(p)
    assert loaded is not None
    assert loaded.schema == SCHEMA_VERSION
    assert loaded.name == "proj"
    assert loaded.goal == "ship v1"
    assert loaded.repo["url"] == "git@x:y.git"
    assert len(loaded.members) == 1
    assert loaded.members[0].id == "@lead"
    assert loaded.members[0].persistent is True
    assert loaded.links["github_issues"].endswith("/issues")


def test_init_project_accepts_new_kwargs(tmp_paths, tmp_path):
    p = tmp_path / "alpha"
    p.mkdir()
    proj = init_project(
        path=p,
        goal="ship alpha",
        repo="git@x:alpha.git",
        members=[{"id": "@lead", "role": "lead", "persistent": False}],
        paths=tmp_paths,
    )
    assert proj.goal == "ship alpha"
    assert proj.repo is not None and proj.repo["url"] == "git@x:alpha.git"
    assert [m.id for m in proj.members] == ["@lead"]
    assert proj.schema == SCHEMA_VERSION
