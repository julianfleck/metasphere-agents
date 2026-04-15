"""End-to-end project lifecycle integration test (layer G).

Runs the full flow on tmp_path with no real telegram / no real tmux:

    project new (with goal, members, persistent flag)
    → MISSION.md auto-written
    → list / show
    → add a task in the project scope
    → send a message in the project scope
    → wake_members (mocked tmux)
    → archive (status flip)
    → list shows status correctly
"""

from pathlib import Path

from metasphere import messages as _messages
from metasphere import tasks as _tasks
from metasphere.project import (
    add_member,
    get_project,
    list_projects,
    load_project,
    new_project,
    save_project,
    wake_members,
)


def test_full_lifecycle(tmp_paths, tmp_path, capsys):
    proj_dir = tmp_paths.project_root / "recurse"
    proj = new_project(
        "recurse",
        path=proj_dir,
        goal="sense-making substrate on RAGE",
        members=[
            {"id": "@orchestrator", "role": "lead", "persistent": True},
            {"id": "@researcher", "role": "researcher", "persistent": True},
        ],
        paths=tmp_paths,
    )
    # ---- schema v2 fields populated ----
    assert proj.schema == 2
    assert proj.goal.startswith("sense-making")
    assert [m.id for m in proj.members] == ["@orchestrator", "@researcher"]
    # Canonical layout (PR #10): project.json + per-project dirs live
    # under ~/.metasphere/projects/<name>/. In-repo ``.metasphere/``
    # stays as a lightweight marker dir for legacy tooling that
    # probes "is this a metasphere project?".
    assert proj_dir.joinpath(".metasphere").is_dir()
    assert (tmp_paths.projects / "recurse" / "project.json").is_file()
    assert (tmp_paths.projects / "recurse" / ".tasks" / "active").is_dir()
    assert (tmp_paths.projects / "recurse" / ".messages" / "inbox").is_dir()

    # ---- persistent members got stub MISSION.md ----
    orch_mission = tmp_paths.agents / "@orchestrator" / "MISSION.md"
    researcher_mission = tmp_paths.agents / "@researcher" / "MISSION.md"
    assert orch_mission.is_file()
    assert researcher_mission.is_file()
    assert "recurse" in orch_mission.read_text()
    assert "sense-making" in orch_mission.read_text()

    # ---- list finds it ----
    rows = list_projects(paths=tmp_paths)
    assert any(r.name == "recurse" and r.status == "active" for r in rows)

    # ---- get_project by name and by path both work ----
    by_name = get_project("recurse", paths=tmp_paths)
    by_path = load_project(proj_dir)
    assert by_name is not None and by_path is not None
    assert by_name.name == by_path.name == "recurse"

    # ---- add a task in the project scope ----
    t = _tasks.create_task(
        "ship v0", priority="!high",
        scope=proj_dir, project_root=tmp_paths.project_root,
    )
    # Canonical-layout tasks live under ~/.metasphere/projects/<name>/
    # .tasks/active/, not in-repo.
    active_dir = tmp_paths.projects / "recurse" / ".tasks" / "active"
    assert any(active_dir.glob("*.md")), f"no task in {active_dir}"
    assert t.title == "ship v0"

    # ---- send a message in the project scope (no real wake, no real telegram) ----
    from dataclasses import replace
    scoped = replace(tmp_paths, scope=proj_dir)
    msg = _messages.send_message(
        target="@.", label="!info", body="hello recurse",
        from_agent="@orchestrator", paths=scoped, wake=False,
    )
    assert msg.path is not None and msg.path.is_file()

    # ---- wake_members with mocked tmux ----
    waked_calls = []
    def fake_waker(name, paths=None):
        waked_calls.append(name)
    waked = wake_members("recurse", paths=tmp_paths, waker=fake_waker)
    assert set(waked) == {"@orchestrator", "@researcher"}
    assert set(waked_calls) == {"@orchestrator", "@researcher"}

    # ---- add a follow-on member after the fact ----
    add_member("recurse", "@reviewer", role="reviewer", paths=tmp_paths)
    assert any(m.id == "@reviewer"
               for m in load_project(proj_dir).members)

    # ---- archive (status flip) ----
    reloaded = load_project(proj_dir)
    reloaded.status = "archived"
    save_project(reloaded)

    rows = list_projects(paths=tmp_paths)
    archived = [r for r in rows if r.name == "recurse"]
    assert archived and archived[0].status == "archived"
