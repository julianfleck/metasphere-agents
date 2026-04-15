"""Member API + new_project constructor + CLI integration (layers B + C)."""

from pathlib import Path

import pytest

from metasphere.cli.project import main as project_cli
from metasphere.project import (
    add_member,
    get_project,
    list_members,
    new_project,
    remove_member,
    wake_members,
)


def test_add_and_remove_member(tmp_paths, tmp_path):
    p = tmp_path / "proj"
    new_project("proj", path=p, paths=tmp_paths)
    add_member("proj", "@lead", role="lead", paths=tmp_paths)
    add_member("proj", "@dev", role="developer", paths=tmp_paths)
    members = list_members("proj", paths=tmp_paths)
    assert [m.id for m in members] == ["@lead", "@dev"]
    remove_member("proj", "@lead", paths=tmp_paths)
    members = list_members("proj", paths=tmp_paths)
    assert [m.id for m in members] == ["@dev"]


def test_add_member_normalizes_at_prefix(tmp_paths, tmp_path):
    new_project("p2", path=tmp_path / "p2", paths=tmp_paths)
    add_member("p2", "lead", role="lead", paths=tmp_paths)  # no @
    assert list_members("p2", paths=tmp_paths)[0].id == "@lead"


def test_add_member_is_idempotent_last_write_wins(tmp_paths, tmp_path):
    new_project("p3", path=tmp_path / "p3", paths=tmp_paths)
    add_member("p3", "@x", role="reviewer", paths=tmp_paths)
    add_member("p3", "@x", role="lead", persistent=True, paths=tmp_paths)
    ms = list_members("p3", paths=tmp_paths)
    assert len(ms) == 1
    assert ms[0].role == "lead"
    assert ms[0].persistent is True


def test_persistent_member_auto_writes_mission(tmp_paths, tmp_path):
    new_project("p4", path=tmp_path / "p4", goal="ship it", paths=tmp_paths)
    add_member("p4", "@workerx", role="lead", persistent=True, paths=tmp_paths)
    mission = tmp_paths.agents / "@workerx" / "MISSION.md"
    assert mission.is_file()
    text = mission.read_text()
    assert "p4" in text
    assert "ship it" in text


def test_persistent_member_preserves_existing_mission(tmp_paths, tmp_path):
    new_project("p5", path=tmp_path / "p5", paths=tmp_paths)
    agent_dir = tmp_paths.agents / "@keep"
    agent_dir.mkdir(parents=True)
    (agent_dir / "MISSION.md").write_text("ORIGINAL\n")
    add_member("p5", "@keep", persistent=True, paths=tmp_paths)
    assert (agent_dir / "MISSION.md").read_text() == "ORIGINAL\n"


def test_wake_members_iterates_persistent_only(tmp_paths, tmp_path):
    new_project("p6", path=tmp_path / "p6", paths=tmp_paths)
    add_member("p6", "@lead", persistent=True, paths=tmp_paths)
    add_member("p6", "@eph", persistent=False, paths=tmp_paths)
    add_member("p6", "@lead2", persistent=True, paths=tmp_paths)

    called = []

    def fake_waker(name, paths=None):
        called.append(name)

    waked = wake_members("p6", paths=tmp_paths, waker=fake_waker)
    assert set(waked) == {"@lead", "@lead2"}
    assert set(called) == {"@lead", "@lead2"}


# ---------- new_project constructor ----------


def test_new_project_defaults_path_to_cwd_slash_name(tmp_paths, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    proj = new_project("alpha", paths=tmp_paths)
    assert Path(proj.path) == (tmp_path / "alpha").resolve()
    # Post-PR #11: project.json lives at canonical location only; the
    # in-repo ``.metasphere/`` is just a marker dir.
    assert (tmp_path / "alpha" / ".metasphere").is_dir()
    assert (tmp_paths.projects / "alpha" / "project.json").is_file()


def test_new_project_with_goal_and_members(tmp_paths, tmp_path):
    proj = new_project(
        "beta", path=tmp_path / "beta", goal="beta goal",
        members=[{"id": "@lead", "role": "lead", "persistent": True}],
        paths=tmp_paths,
    )
    assert proj.goal == "beta goal"
    assert proj.members[0].id == "@lead"
    assert (tmp_paths.agents / "@lead" / "MISSION.md").is_file()


def test_new_project_auto_clones_when_repo_and_path_missing(tmp_paths, tmp_path):
    calls = []

    def fake_clone(url, dest):
        calls.append((url, Path(dest)))
        Path(dest).mkdir(parents=True)
        (Path(dest) / "README").write_text("cloned\n")

    proj = new_project(
        "gamma", path=tmp_path / "gamma",
        repo="git@x:y.git", paths=tmp_paths,
        git_clone=fake_clone,
    )
    assert calls == [("git@x:y.git", (tmp_path / "gamma").resolve())]
    assert proj.repo is not None and proj.repo["url"] == "git@x:y.git"
    assert (tmp_path / "gamma" / "README").is_file()


def test_new_project_errors_when_repo_and_path_nonempty(tmp_paths, tmp_path):
    existing = tmp_path / "delta"
    existing.mkdir()
    (existing / "file.txt").write_text("x")
    with pytest.raises(FileExistsError):
        new_project(
            "delta", path=existing, repo="git@x:y.git", paths=tmp_paths,
            git_clone=lambda u, d: None,
        )


# ---------- CLI integration ----------


def test_cli_new_and_show(tmp_paths, tmp_path, capsys):
    rc = project_cli([
        "new", "epsilon",
        "--path", str(tmp_path / "epsilon"),
        "--goal", "the goal",
        "--member", "@lead:lead:persistent",
        "--member", "@dev:developer",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Created project: epsilon" in out

    rc = project_cli(["show", "epsilon"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "epsilon" in out and "the goal" in out
    assert "@lead" in out and "persistent" in out


def test_cli_member_add_remove_list(tmp_paths, tmp_path, capsys):
    project_cli(["new", "zeta", "--path", str(tmp_path / "zeta")])
    capsys.readouterr()
    assert project_cli(["member", "add", "zeta", "@lead", "--role", "lead",
                        "--persistent"]) == 0
    capsys.readouterr()
    assert project_cli(["members", "zeta"]) == 0
    out = capsys.readouterr().out
    assert "@lead" in out and "lead" in out
    assert project_cli(["member", "remove", "zeta", "@lead"]) == 0
    capsys.readouterr()
    assert project_cli(["members", "zeta"]) == 0
    out = capsys.readouterr().out
    assert "@lead" not in out


def test_cli_list(tmp_paths, tmp_path, capsys):
    project_cli(["new", "eta", "--path", str(tmp_path / "eta")])
    capsys.readouterr()
    assert project_cli(["list"]) == 0
    out = capsys.readouterr().out
    assert "eta" in out
