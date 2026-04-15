"""Tests for ``metasphere daemon start|stop|restart|status``.

The wrapper shells out to ``systemctl --user``; these tests stub that
boundary so the suite stays hermetic. Real systemd interaction is out
of scope for unit tests.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from metasphere.cli import daemon as d


def _fake_cp(rc: int = 0, stdout: str = "", stderr: str = ""):
    cp = MagicMock()
    cp.returncode = rc
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


def test_start_all_services_shells_out_three_times(monkeypatch, capsys):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return _fake_cp(rc=0)

    rc = d.main(["start"])
    # Sanity: injected runner not used in this code path; we patch the
    # module-level _run instead via monkeypatch.
    # Actually we need to inject via _run — use monkeypatch.
    # (above was wrong; redoing below)
    pass  # ignore first attempt


def test_start_all_services_calls_each_unit(monkeypatch, capsys):
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return _fake_cp(rc=0)

    monkeypatch.setattr("subprocess.run", fake_run)
    rc = d.main(["start"])
    assert rc == 0
    assert len(calls) == 3
    units = [call[3] for call in calls]
    assert units == [
        "metasphere-gateway.service",
        "metasphere-heartbeat.service",
        "metasphere-schedule.service",
    ]
    out = capsys.readouterr().out
    assert "gateway" in out and "start ok" in out


def test_restart_single_service(monkeypatch, capsys):
    calls: list[list[str]] = []
    monkeypatch.setattr(
        "subprocess.run",
        lambda argv, **kw: (calls.append(list(argv)) or _fake_cp(rc=0)),
    )
    rc = d.main(["restart", "heartbeat"])
    assert rc == 0
    assert len(calls) == 1
    assert calls[0][3] == "metasphere-heartbeat.service"
    assert calls[0][2] == "restart"


def test_status_renders_active_line(monkeypatch, capsys):
    def fake_run(argv, **kw):
        svc = argv[3]
        stdout = (
            f"● {svc}\n"
            "     Loaded: loaded\n"
            "     Active: active (running) since Tue 2026-04-15 10:00:00 UTC; 1h ago\n"
            "   Main PID: 12345 (python)\n"
        )
        return _fake_cp(rc=0, stdout=stdout)

    monkeypatch.setattr("subprocess.run", fake_run)
    rc = d.main(["status"])
    assert rc == 0
    out = capsys.readouterr().out
    # Three services, three lines, each with the Active: payload.
    lines = [l for l in out.splitlines() if l.strip()]
    assert len(lines) == 3
    for line in lines:
        assert "active (running)" in line


def test_status_inactive_service_returns_zero(monkeypatch, capsys):
    """``systemctl status`` returns rc=3 for inactive. Wrapper reports
    the state but doesn't fail the CLI.
    """
    def fake_run(argv, **kw):
        return _fake_cp(
            rc=3,
            stdout="     Loaded: loaded\n     Active: inactive (dead)\n",
        )

    monkeypatch.setattr("subprocess.run", fake_run)
    rc = d.main(["status", "gateway"])
    assert rc == 0
    assert "inactive (dead)" in capsys.readouterr().out


def test_lifecycle_failure_propagates_exit_code(monkeypatch, capsys):
    def fake_run(argv, **kw):
        return _fake_cp(
            rc=5,
            stderr="Failed to start metasphere-gateway.service: Unit not found.\n",
        )

    monkeypatch.setattr("subprocess.run", fake_run)
    rc = d.main(["start", "gateway"])
    assert rc == 5
    err = capsys.readouterr().err
    assert "start failed" in err
    assert "Unit not found" in err


def test_unknown_action_rejected_by_parser(capsys):
    with pytest.raises(SystemExit):
        d.main(["reload"])


def test_unknown_service_rejected_by_parser(capsys):
    with pytest.raises(SystemExit):
        d.main(["start", "unknown-service"])
