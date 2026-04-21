"""Tests for metasphere.gateway.monitoring — counters, thresholds, ALERT."""

from __future__ import annotations

from unittest.mock import patch

from metasphere import context as ctx
from metasphere.cli import gateway as cli_gateway
from metasphere.gateway import monitoring as mon
from metasphere.paths import Paths


# ---------------------------------------------------------------------------
# Snapshot factory
# ---------------------------------------------------------------------------

def _snap(*, z_total=0, z_npm=0, t_total=0, t_pers=0, t_eph=0,
          pid_limit=1000, pid_current=100, pid_free_pct=90.0,
          pid_source="kernel") -> mon.MonitoringSnapshot:
    return mon.MonitoringSnapshot(
        zombies=mon.ZombieCounters(total=z_total, npm_root_g=z_npm),
        tmux=mon.TmuxCounters(total=t_total, persistent=t_pers, ephemeral=t_eph),
        pids=mon.PidHeadroom(limit=pid_limit, current=pid_current,
                             free_pct=pid_free_pct, source=pid_source),
    )


# ---------------------------------------------------------------------------
# ALERT thresholds
# ---------------------------------------------------------------------------

def test_no_trip_emits_nothing():
    snap = _snap()
    assert mon.evaluate_alert(snap) == ""


def test_zombie_threshold_trips_alone():
    snap = _snap(z_total=25, z_npm=20)
    alert = mon.evaluate_alert(snap)
    assert alert.startswith("## ALERT:")
    assert "zombies=25" in alert
    assert "npm_root_g=20" in alert
    assert "tmux_sessions" not in alert
    assert "pid_headroom" not in alert


def test_tmux_threshold_trips_alone():
    snap = _snap(t_total=15, t_pers=3, t_eph=12)
    alert = mon.evaluate_alert(snap)
    assert alert.startswith("## ALERT:")
    assert "tmux_sessions=15" in alert
    assert "persistent=3" in alert
    assert "ephemeral=12" in alert
    assert "zombies" not in alert
    assert "pid_headroom" not in alert


def test_pid_headroom_threshold_trips_alone():
    snap = _snap(pid_limit=1000, pid_current=850, pid_free_pct=15.0)
    alert = mon.evaluate_alert(snap)
    assert alert.startswith("## ALERT:")
    assert "pid_headroom=15.0%" in alert
    assert "850/1000" in alert
    assert "zombies" not in alert
    assert "tmux_sessions" not in alert


def test_multiple_thresholds_trip_together():
    snap = _snap(
        z_total=30, z_npm=28,
        t_total=20, t_pers=5, t_eph=15,
        pid_limit=1000, pid_current=900, pid_free_pct=10.0,
    )
    alert = mon.evaluate_alert(snap)
    assert alert.startswith("## ALERT:")
    # All three trip conditions present, joined by semicolons.
    assert "zombies=30" in alert
    assert "tmux_sessions=20" in alert
    assert "pid_headroom=10.0%" in alert
    assert alert.count(";") >= 2


def test_boundary_equal_to_threshold_does_not_trip():
    """Guard: zombies == 20 (not >), tmux == 10 (not >),
    pid_pct == 20 (not <) must all be silent."""
    snap = _snap(
        z_total=mon.ZOMBIE_THRESHOLD,
        t_total=mon.TMUX_THRESHOLD,
        pid_free_pct=float(mon.PID_HEADROOM_PCT_THRESHOLD),
    )
    assert mon.evaluate_alert(snap) == ""


# ---------------------------------------------------------------------------
# render_alert — env override path
# ---------------------------------------------------------------------------

def test_render_alert_env_override_fires(monkeypatch, tmp_paths: Paths):
    monkeypatch.setenv("METASPHERE_MONITORING_OVERRIDE",
                        "zombies=50,tmux=3,pid_pct=99.0")
    out = mon.render_alert(tmp_paths)
    assert out.startswith("## ALERT:")
    assert "zombies=50" in out


def test_render_alert_env_override_silent_when_below_thresholds(
    monkeypatch, tmp_paths: Paths,
):
    monkeypatch.setenv("METASPHERE_MONITORING_OVERRIDE",
                        "zombies=5,tmux=3,pid_pct=99.0")
    out = mon.render_alert(tmp_paths)
    assert out == ""


def test_render_alert_swallows_probe_exceptions(monkeypatch, tmp_paths: Paths):
    """A failing probe must never break the turn — fail closed to ''."""
    monkeypatch.delenv("METASPHERE_MONITORING_OVERRIDE", raising=False)

    def boom(_paths):
        raise RuntimeError("simulated probe failure")

    monkeypatch.setattr(mon, "snapshot", boom)
    assert mon.render_alert(tmp_paths) == ""


# ---------------------------------------------------------------------------
# render_status — gateway status output
# ---------------------------------------------------------------------------

def test_render_status_contains_all_three_sections(tmp_paths: Paths):
    fake = _snap(
        z_total=3, z_npm=1,
        t_total=4, t_pers=2, t_eph=2,
        pid_limit=4194304, pid_current=450, pid_free_pct=99.99,
        pid_source="kernel",
    )
    with patch.object(mon, "snapshot", return_value=fake):
        out = mon.render_status(tmp_paths)
    assert "zombies total=3 npm_root_g=1" in out
    assert "tmux total=4 persistent=2 ephemeral=2" in out
    assert "pid_headroom limit=4194304 current=450" in out
    assert "source=kernel" in out


def test_render_status_shows_unlimited_when_no_limit(tmp_paths: Paths):
    fake = _snap(pid_limit=0, pid_current=200, pid_free_pct=100.0,
                 pid_source="unknown")
    with patch.object(mon, "snapshot", return_value=fake):
        out = mon.render_status(tmp_paths)
    assert "limit=unlimited" in out


# ---------------------------------------------------------------------------
# tmux_counters — persistent-vs-ephemeral split via MISSION.md
# ---------------------------------------------------------------------------

def test_tmux_counters_splits_persistent_from_ephemeral(
    tmp_paths: Paths, monkeypatch,
):
    # Two global agents: @persistent has MISSION.md, @scratch does not.
    persistent_dir = tmp_paths.agents / "@persistent"
    persistent_dir.mkdir(parents=True, exist_ok=True)
    (persistent_dir / "MISSION.md").write_text("keep me alive")
    scratch_dir = tmp_paths.agents / "@scratch"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    # No MISSION.md -> ephemeral.

    fake_sessions = [
        "metasphere-persistent",
        "metasphere-scratch",
        "metasphere-stray-session",  # orphan, no agent dir -> ephemeral
    ]
    monkeypatch.setattr(mon, "_tmux_list_sessions", lambda: fake_sessions)
    c = mon.tmux_counters(tmp_paths)
    assert c.total == 3
    assert c.persistent == 1
    assert c.ephemeral == 2


def test_tmux_counters_recognises_project_scoped_persistent(
    tmp_paths: Paths, monkeypatch,
):
    # Project-scoped: metasphere-<project>-<agent>
    project_agent = tmp_paths.projects / "worldwire" / "agents" / "@analyst"
    project_agent.mkdir(parents=True, exist_ok=True)
    (project_agent / "MISSION.md").write_text("analyse")

    monkeypatch.setattr(
        mon, "_tmux_list_sessions",
        lambda: ["metasphere-worldwire-analyst"],
    )
    c = mon.tmux_counters(tmp_paths)
    assert c.total == 1
    assert c.persistent == 1
    assert c.ephemeral == 0


def test_tmux_counters_empty_when_no_tmux(monkeypatch, tmp_paths: Paths):
    monkeypatch.setattr(mon, "_tmux_list_sessions", lambda: [])
    c = mon.tmux_counters(tmp_paths)
    assert c.total == 0
    assert c.persistent == 0
    assert c.ephemeral == 0


# ---------------------------------------------------------------------------
# build_context — ALERT at the top
# ---------------------------------------------------------------------------

def test_build_context_places_alert_above_status_header(
    tmp_paths: Paths, monkeypatch,
):
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@orchestrator")
    monkeypatch.setenv("METASPHERE_MONITORING_OVERRIDE",
                        "zombies=100,tmux=0,pid_pct=99.9")
    # Seed minimal agent dir so status_header has something to read.
    agent_dir = tmp_paths.agents / "@orchestrator"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "status").write_text("active: testing")

    out = ctx.build_context(tmp_paths)
    # ALERT is the first non-empty block.
    first_chunks = [line for line in out.splitlines() if line.strip()]
    assert first_chunks, "context must produce output"
    assert first_chunks[0].startswith("## ALERT:"), (
        f"expected ALERT line at top, got: {first_chunks[0]!r}"
    )
    # Status header follows somewhere after.
    assert any("Metasphere Delta" in line for line in first_chunks)


def test_build_context_no_alert_when_thresholds_silent(
    tmp_paths: Paths, monkeypatch,
):
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@orchestrator")
    monkeypatch.setenv("METASPHERE_MONITORING_OVERRIDE",
                        "zombies=1,tmux=1,pid_pct=99.9")
    agent_dir = tmp_paths.agents / "@orchestrator"
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "status").write_text("active: testing")

    out = ctx.build_context(tmp_paths)
    assert "## ALERT:" not in out


# ---------------------------------------------------------------------------
# CLI gateway status — includes monitoring block
# ---------------------------------------------------------------------------

def test_cli_gateway_status_prints_monitoring(capsys, tmp_paths: Paths, monkeypatch):
    # Force resolve() to return our tmp_paths.
    monkeypatch.setattr(cli_gateway, "resolve", lambda: tmp_paths)
    monkeypatch.setattr(cli_gateway, "session_health", lambda p: (True, 5))
    fake = _snap(z_total=2, z_npm=1, t_total=3, t_pers=1, t_eph=2,
                 pid_limit=1000, pid_current=10, pid_free_pct=99.0)
    monkeypatch.setattr(mon, "snapshot", lambda paths: fake)

    import argparse
    rc = cli_gateway.cmd_status(argparse.Namespace())
    captured = capsys.readouterr().out
    assert rc == 0
    assert "session=metasphere-orchestrator" in captured
    assert "zombies total=2" in captured
    assert "tmux total=3" in captured
    assert "pid_headroom limit=1000" in captured
