"""Tests for ``metasphere daemon start|stop|restart|status``.

The wrapper shells out to ``systemctl --user``; these tests stub that
boundary so the suite stays hermetic. Real systemd interaction is out
of scope for unit tests.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from metasphere.cli import daemon as d


@pytest.fixture(autouse=True)
def _systemd_platform(monkeypatch):
    monkeypatch.setattr(d, "_is_macos", lambda: False)


def _fake_cp(rc: int = 0, stdout: str = "", stderr: str = ""):
    cp = MagicMock()
    cp.returncode = rc
    cp.stdout = stdout
    cp.stderr = stderr
    return cp


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


def test_launchd_start_bootstraps_unloaded_service(monkeypatch):
    monkeypatch.setattr(d, "_is_macos", lambda: True)
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        if argv[:2] == ["launchctl", "print"]:
            return _fake_cp(rc=113, stderr="Could not find service")
        return _fake_cp(rc=0)

    monkeypatch.setattr("subprocess.run", fake_run)
    rc = d.main(["start", "gateway"])
    assert rc == 0
    assert calls[-1][:3] == ["launchctl", "bootstrap", f"gui/{d.os.getuid()}"]
    assert calls[-1][-1].endswith("com.metasphere.gateway.plist")


def test_launchd_restart_reloads_loaded_service_definition(monkeypatch):
    monkeypatch.setattr(d, "_is_macos", lambda: True)
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        return _fake_cp(rc=0)

    monkeypatch.setattr("subprocess.run", fake_run)
    rc = d.main(["restart", "heartbeat"])
    assert rc == 0
    target = f"gui/{d.os.getuid()}/com.metasphere.heartbeat"
    assert calls == [
        ["launchctl", "print", target],
        ["launchctl", "bootout", target],
        [
            "launchctl",
            "bootstrap",
            f"gui/{d.os.getuid()}",
            d._launchd_plist("heartbeat"),
        ],
    ]


def test_launchd_restart_bootstraps_unloaded_service(monkeypatch):
    monkeypatch.setattr(d, "_is_macos", lambda: True)
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        if argv[:2] == ["launchctl", "print"]:
            return _fake_cp(rc=113, stderr="Could not find service")
        return _fake_cp(rc=0)

    monkeypatch.setattr("subprocess.run", fake_run)
    assert d.main(["restart", "schedule"]) == 0
    assert ["launchctl", "bootout"] not in [call[:2] for call in calls]
    assert calls[-1] == [
        "launchctl",
        "bootstrap",
        f"gui/{d.os.getuid()}",
        d._launchd_plist("schedule"),
    ]


def test_launchd_status_renders_state_and_pid(monkeypatch, capsys):
    monkeypatch.setattr(d, "_is_macos", lambda: True)

    def fake_run(argv, **kwargs):
        return _fake_cp(rc=0, stdout="state = running\npid = 4242\n")

    monkeypatch.setattr("subprocess.run", fake_run)
    rc = d.main(["status", "schedule"])
    assert rc == 0
    assert "running (pid 4242)" in capsys.readouterr().out
