"""Tests for metasphere.liveness (tmux-pane-freshness liveness probe)."""

from __future__ import annotations

import json

from metasphere import liveness
from metasphere.agents import AgentRecord
from metasphere.liveness import (
    DEAD,
    GENERATING,
    IDLE,
    STALE,
    UNKNOWN,
    Liveness,
    active_projects,
    agent_liveness,
    format_liveness,
    liveness_snapshot,
)
from metasphere.paths import Paths


def _agent(name="@widget-eng", project="widget", status="working: cutover"):
    return AgentRecord(
        name=name,
        scope="",
        parent="@orchestrator",
        status=status,
        spawned_at="2026-06-29T00:00:00Z",
        project=project,
    )


def _prober(text):
    """A prober returning fixed pane text (None = dead session)."""
    return lambda _session: text


# ---------------------------------------------------------------------------
# agent_liveness — state machine
# ---------------------------------------------------------------------------

def test_dead_when_no_session(tmp_paths: Paths):
    lv = agent_liveness(_agent(), paths=tmp_paths, now=1000, prober=lambda s: None)
    assert lv.state == DEAD
    assert lv.idle_age_s is None
    assert not lv.is_alive


def test_cold_start_without_indicator_is_unknown(tmp_paths: Paths):
    lv = agent_liveness(
        _agent(), paths=tmp_paths, now=1000, prober=_prober("quiet pane\n❯ ")
    )
    assert lv.state == UNKNOWN
    assert lv.idle_age_s is None


def test_cold_start_with_indicator_is_generating(tmp_paths: Paths):
    pane = "doing work\n  ⏵⏵ bypass · 1 shell · esc to interrupt · ↓ to manage"
    lv = agent_liveness(_agent(), paths=tmp_paths, now=1000, prober=_prober(pane))
    assert lv.state == GENERATING
    assert lv.idle_age_s == 0


def test_changed_pane_is_generating(tmp_paths: Paths):
    a = _agent()
    agent_liveness(a, paths=tmp_paths, now=1000, prober=_prober("frame one\n❯ "))
    lv = agent_liveness(a, paths=tmp_paths, now=1005, prober=_prober("frame two\n❯ "))
    assert lv.state == GENERATING
    assert lv.idle_age_s == 0


def test_unchanged_within_window_is_idle(tmp_paths: Paths):
    a = _agent()
    agent_liveness(a, paths=tmp_paths, now=1000, prober=_prober("same\n❯ "))
    lv = agent_liveness(
        a, paths=tmp_paths, now=1300, stale_after_s=600, prober=_prober("same\n❯ ")
    )
    assert lv.state == IDLE
    assert lv.idle_age_s == 300


def test_unchanged_beyond_window_is_stale(tmp_paths: Paths):
    a = _agent()
    agent_liveness(a, paths=tmp_paths, now=1000, prober=_prober("same\n❯ "))
    lv = agent_liveness(
        a, paths=tmp_paths, now=2000, stale_after_s=600, prober=_prober("same\n❯ ")
    )
    assert lv.state == STALE
    assert lv.idle_age_s == 1000


def test_indicator_overrides_unchanged_to_generating(tmp_paths: Paths):
    # Pane text identical across captures but the footer shows the indicator:
    # a tool is running even though the tail didn't move this tick.
    pane = "thinking\n  ⏵⏵ esc to interrupt"
    a = _agent()
    agent_liveness(a, paths=tmp_paths, now=1000, prober=_prober(pane))
    lv = agent_liveness(
        a, paths=tmp_paths, now=2000, stale_after_s=600, prober=_prober(pane)
    )
    assert lv.state == GENERATING


def test_probe_never_raises_on_prober_error(tmp_paths: Paths):
    def boom(_session):
        raise RuntimeError("tmux exploded")

    lv = agent_liveness(_agent(), paths=tmp_paths, now=1000, prober=boom)
    assert lv.state == UNKNOWN


# ---------------------------------------------------------------------------
# Snapshot persistence
# ---------------------------------------------------------------------------

def test_snapshot_written_to_state_dir(tmp_paths: Paths):
    a = _agent()
    agent_liveness(a, paths=tmp_paths, now=1000, prober=_prober("x\n❯ "))
    snap_dir = tmp_paths.state / "liveness"
    files = list(snap_dir.glob("*.json"))
    assert len(files) == 1
    data = json.loads(files[0].read_text())
    assert {"hash", "captured_at", "last_change"} <= data.keys()
    assert data["last_change"] == 1000


def test_persist_false_does_not_write(tmp_paths: Paths):
    agent_liveness(
        _agent(), paths=tmp_paths, now=1000, persist=False, prober=_prober("x")
    )
    assert not (tmp_paths.state / "liveness").exists()


def test_last_change_preserved_across_unchanged_captures(tmp_paths: Paths):
    a = _agent()
    agent_liveness(a, paths=tmp_paths, now=1000, prober=_prober("same\n❯ "))
    agent_liveness(a, paths=tmp_paths, now=1500, prober=_prober("same\n❯ "))
    files = list((tmp_paths.state / "liveness").glob("*.json"))
    data = json.loads(files[0].read_text())
    # captured_at advances, last_change stays at the first sighting.
    assert data["captured_at"] == 1500
    assert data["last_change"] == 1000


# ---------------------------------------------------------------------------
# doing-distillation
# ---------------------------------------------------------------------------

def test_doing_from_status_extracts_detail():
    assert liveness._doing_from_status("working: OPS-1 vault PR") == "OPS-1 vault PR"
    assert liveness._doing_from_status("spawned: scan markets") == "scan markets"


def test_doing_from_status_collapses_lifecycle_noise():
    assert liveness._doing_from_status("active: persistent session") == ""
    assert liveness._doing_from_status("dormant: idle 4000s") == ""
    assert liveness._doing_from_status("") == ""


def test_doing_from_status_keeps_complete_summary():
    assert liveness._doing_from_status("complete: shipped PR #204") == "shipped PR #204"


# ---------------------------------------------------------------------------
# Threshold env override
# ---------------------------------------------------------------------------

def test_stale_threshold_env_override(tmp_paths: Paths, monkeypatch):
    monkeypatch.setenv("METASPHERE_LIVENESS_STALE_AFTER_S", "100")
    a = _agent()
    agent_liveness(a, paths=tmp_paths, now=1000, prober=_prober("same\n❯ "))
    lv = agent_liveness(a, paths=tmp_paths, now=1200, prober=_prober("same\n❯ "))
    assert lv.state == STALE  # 200s > 100s override


def test_bad_env_falls_back_to_default(tmp_paths: Paths, monkeypatch):
    monkeypatch.setenv("METASPHERE_LIVENESS_STALE_AFTER_S", "not-an-int")
    a = _agent()
    agent_liveness(a, paths=tmp_paths, now=1000, prober=_prober("same\n❯ "))
    lv = agent_liveness(a, paths=tmp_paths, now=1200, prober=_prober("same\n❯ "))
    assert lv.state == IDLE  # 200s < 600s default


# ---------------------------------------------------------------------------
# liveness_snapshot — cross-project aggregation
# ---------------------------------------------------------------------------

def _make_agent_dir(paths: Paths, name: str, project: str, status: str, persistent=True):
    adir = paths.project_agents_dir(project) / name
    adir.mkdir(parents=True, exist_ok=True)
    (adir / "scope").write_text("")
    (adir / "parent").write_text("@orchestrator")
    (adir / "status").write_text(status)
    (adir / "spawned_at").write_text("2026-06-29T00:00:00Z")
    if persistent:
        (adir / "MISSION.md").write_text("mission")
    return adir


def test_liveness_snapshot_groups_and_sorts(tmp_paths: Paths):
    _make_agent_dir(tmp_paths, "@eng", "widget", "working: cutover")
    _make_agent_dir(tmp_paths, "@lead", "mesa", "active: persistent session")

    # Prober keys off session name to give distinct states.
    def prober(session):
        if "eng" in session:
            return "doing\n  esc to interrupt"
        return "idle pane\n❯ "

    items = liveness_snapshot(paths=tmp_paths, now=1000, prober=prober)
    # mesa sorts before widget.
    assert [lv.project for lv in items] == ["mesa", "widget"]
    states = {lv.agent: lv.state for lv in items}
    assert states["@eng"] == GENERATING


def test_liveness_snapshot_persistent_only_filter(tmp_paths: Paths):
    _make_agent_dir(tmp_paths, "@lead", "widget", "working: x", persistent=True)
    _make_agent_dir(tmp_paths, "@scout", "widget", "spawned: y", persistent=False)
    items = liveness_snapshot(paths=tmp_paths, now=1000, prober=_prober("p\n❯ "))
    names = {lv.agent for lv in items}
    assert "@lead" in names
    assert "@scout" not in names

    items_all = liveness_snapshot(
        paths=tmp_paths, now=1000, prober=_prober("p\n❯ "), persistent_only=False
    )
    assert "@scout" in {lv.agent for lv in items_all}


def test_liveness_snapshot_drops_dead_unless_requested(tmp_paths: Paths):
    _make_agent_dir(tmp_paths, "@lead", "widget", "working: x")
    items = liveness_snapshot(paths=tmp_paths, now=1000, prober=lambda s: None)
    assert items == []
    items_dead = liveness_snapshot(
        paths=tmp_paths, now=1000, prober=lambda s: None, include_dead=True
    )
    assert [lv.state for lv in items_dead] == [DEAD]


# ---------------------------------------------------------------------------
# format_liveness
# ---------------------------------------------------------------------------

def test_format_liveness_renders_grouped_view():
    items = [
        Liveness("@widget-eng", "widget", "s1", GENERATING, 1, "Phase-1 cutover"),
        Liveness("@widget-lead", "widget", "s2", IDLE, 240, ""),
    ]
    out = format_liveness(items)
    assert "widget" in out
    assert "● @widget-eng" in out
    assert "generating · Phase-1 cutover" in out
    assert "○ @widget-lead" in out
    assert "idle 4m" in out


def test_format_liveness_empty():
    assert format_liveness([]) == "(no agents)"


# ---------------------------------------------------------------------------
# active_projects — task-file-independent activity oracle
# ---------------------------------------------------------------------------

def test_active_projects_unions_generating_excludes_idle(tmp_paths: Paths):
    """Only projects with a generating agent are returned; an idle project
    (no generating agent) is excluded even though its session is alive."""
    _make_agent_dir(tmp_paths, "@eng", "widget", "working: cutover")
    _make_agent_dir(tmp_paths, "@lead", "mesa", "active: persistent session")

    def prober(session):
        return "doing\n  esc to interrupt" if "eng" in session else "quiet\n❯ "

    assert active_projects(paths=tmp_paths, now=1000, prober=prober) == {"widget"}


def test_active_projects_empty_when_none_generating(tmp_paths: Paths):
    """No generating agent anywhere → empty set (the dormant-sweep then
    relies solely on its filed-task signal)."""
    _make_agent_dir(tmp_paths, "@eng", "widget", "working: x")
    assert active_projects(paths=tmp_paths, now=1000, prober=_prober("quiet\n❯ ")) == set()


def test_active_projects_ignores_dead_sessions(tmp_paths: Paths):
    """A dead session contributes no project (probe returns DEAD → dropped
    by liveness_snapshot before the is_working filter)."""
    _make_agent_dir(tmp_paths, "@eng", "widget", "working: x")
    assert active_projects(paths=tmp_paths, now=1000, prober=lambda s: None) == set()
