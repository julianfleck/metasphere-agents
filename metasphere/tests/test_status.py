"""Tests for metasphere.status.summary()."""

from __future__ import annotations

from metasphere import status, tasks as t


def test_summary_reports_task_count(tmp_paths, monkeypatch):
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@tester")
    t.create_task("Alpha", "!normal", tmp_paths.scope, tmp_paths.project_root)
    t.create_task("Beta", "!high", tmp_paths.scope, tmp_paths.project_root)

    out = status.summary()

    assert "Tasks: 2 open (2 pending) across 1 project" in out
    assert "Tasks: (unavailable)" not in out


def test_summary_tasks_zero_when_empty(tmp_paths):
    out = status.summary()
    assert "Tasks: 0 open" in out


def test_summary_counts_blocked_and_paused_open_work(tmp_paths, monkeypatch):
    """Bug B regression — blocked + paused are OPEN work and must be counted,
    not silently dropped by a pending/in-progress allowlist. This was the
    "0 active" symptom: widget's 13 paused tasks read as zero.
    """
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@tester")
    t.create_task("Pend", "!normal", tmp_paths.scope, tmp_paths.project_root)
    b = t.create_task("Block", "!high", tmp_paths.scope, tmp_paths.project_root)
    c = t.create_task("Pause", "!low", tmp_paths.scope, tmp_paths.project_root)
    t.update_task(b.id, tmp_paths.project_root, status="blocked")
    t.update_task(c.id, tmp_paths.project_root, status="paused")

    out = status.summary()

    assert "Tasks: 3 open" in out
    assert "1 pending" in out
    assert "1 blocked" in out
    assert "1 paused" in out


def test_summary_excludes_terminal_tasks(tmp_paths, monkeypatch):
    """A terminal status (completed/abandoned) is never counted as open, even
    if the file still lingers in active/ before archival.
    """
    monkeypatch.setenv("METASPHERE_AGENT_ID", "@tester")
    t.create_task("Keep", "!normal", tmp_paths.scope, tmp_paths.project_root)
    done = t.create_task("Done", "!normal", tmp_paths.scope, tmp_paths.project_root)
    t.update_task(done.id, tmp_paths.project_root, status="completed")

    out = status.summary()

    assert "Tasks: 1 open (1 pending) across 1 project" in out


def test_summary_surfaces_exception_diagnostic(tmp_paths, monkeypatch):
    """Bug-masking guard: a broken subsystem must surface the exception
    inline, not silently render as `(unavailable)`. The original
    `list_tasks` TypeError (KNOWN_ISSUES.md, fixed 2026-04-24) sat
    masked for weeks because the bare handler ate the diagnostic.
    """

    def boom(*_args, **_kwargs):
        raise TypeError("simulated wiring breakage")

    monkeypatch.setattr("metasphere.tasks.list_tasks", boom)

    out = status.summary()

    assert "Tasks: (unavailable: TypeError: simulated wiring breakage)" in out


def test_summary_reports_daemon_health(tmp_paths, monkeypatch):
    """Daemon block surfaces per-daemon active/inactive state. Operators
    rely on it to spot a silently dead heartbeat or schedule daemon —
    the REPL keeps looking healthy in those cases, so the daemon block
    is the only signal."""

    def fake_health():
        return {
            "metasphere-heartbeat": True,
            "metasphere-gateway": True,
            "metasphere-schedule": False,
        }

    monkeypatch.setattr("metasphere.cli.restart.daemon_health", fake_health)

    out = status.summary()

    assert "Daemons:" in out
    assert "● metasphere-heartbeat: active" in out
    assert "● metasphere-gateway: active" in out
    assert "○ metasphere-schedule: inactive" in out


def test_summary_surfaces_daemon_exception(tmp_paths, monkeypatch):
    """Same silent-fail guard as the tasks subsystem: if daemon_health
    blows up, the diagnostic must reach the rendered output instead of
    rendering as bare ``(unavailable)``."""

    def boom():
        raise RuntimeError("systemctl missing")

    monkeypatch.setattr("metasphere.cli.restart.daemon_health", boom)

    out = status.summary()

    assert "Daemons: (unavailable: RuntimeError: systemctl missing)" in out
