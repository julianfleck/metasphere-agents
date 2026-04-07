"""Tests for metasphere.gateway (session + watchdog + daemon)."""

from __future__ import annotations

from unittest.mock import patch, MagicMock

import pytest

from metasphere.gateway import session as gw_session
from metasphere.gateway import watchdog as gw_watchdog
from metasphere.gateway import daemon as gw_daemon
from metasphere.paths import Paths


# ---------------------------------------------------------------------------
# session_health
# ---------------------------------------------------------------------------

def test_session_health_dead_when_no_session(tmp_paths: Paths):
    with patch.object(gw_session, "_agents_session_alive", return_value=False):
        alive, idle = gw_session.session_health(tmp_paths)
    assert alive is False
    assert idle == 0


def test_session_health_alive_with_idle(tmp_paths: Paths):
    disp = MagicMock(returncode=0, stdout="0\n", stderr="")
    with patch.object(gw_session, "_agents_session_alive", return_value=True), \
         patch.object(gw_session, "_tmux", return_value=disp):
        alive, idle = gw_session.session_health(tmp_paths)
    assert alive is True
    assert idle >= 0


# ---------------------------------------------------------------------------
# ensure_session / start_session
# ---------------------------------------------------------------------------

def test_ensure_session_starts_when_dead(tmp_paths: Paths):
    calls = []

    def fake_tmux(*args):
        calls.append(args)
        if args[:1] == ("has-session",):
            return MagicMock(returncode=1, stdout="", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch.object(gw_session, "_tmux", side_effect=fake_tmux), \
         patch.object(gw_session, "session_alive", return_value=False):
        gw_session.ensure_session(tmp_paths)

    cmds = [c[0] for c in calls]
    assert "new-session" in cmds


def test_ensure_session_noop_when_alive(tmp_paths: Paths):
    with patch.object(gw_session, "session_alive", return_value=True), \
         patch.object(gw_session, "_tmux") as m:
        gw_session.ensure_session(tmp_paths)
    assert m.call_count == 0


# ---------------------------------------------------------------------------
# Watchdog: stuck paste
# ---------------------------------------------------------------------------

def test_check_stuck_paste_detects_and_force_enters(tmp_paths: Paths):
    pane = "some output\n[Pasted text #3 +12 lines]\n> "
    with patch.object(gw_watchdog, "session_alive", return_value=True), \
         patch.object(gw_watchdog, "_capture_pane", return_value=pane), \
         patch.object(gw_watchdog, "_send_keys") as send:
        # First tick: write timer baseline, no Enter yet
        first = gw_watchdog.check_stuck_paste(paths=tmp_paths, now=1000)
        assert first is False
        assert send.call_count == 0
        # Second tick: 16s later → force Enter
        second = gw_watchdog.check_stuck_paste(paths=tmp_paths, now=1016)
        assert second is True
        send.assert_called_once()
        assert "Enter" in send.call_args.args


def test_check_stuck_paste_clears_when_placeholder_gone(tmp_paths: Paths):
    state = tmp_paths.state / "stuck_paste_seen"
    state.parent.mkdir(parents=True, exist_ok=True)
    state.write_text("999")
    with patch.object(gw_watchdog, "session_alive", return_value=True), \
         patch.object(gw_watchdog, "_capture_pane", return_value="clean pane"), \
         patch.object(gw_watchdog, "_send_keys") as send:
        result = gw_watchdog.check_stuck_paste(paths=tmp_paths, now=1000)
    assert result is False
    assert not state.exists()
    assert send.call_count == 0


# ---------------------------------------------------------------------------
# Watchdog: safety hooks
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("pane", [
    "Some prompt\nDo you want to proceed?\n  1. Yes\n  2. No",
    "[plugin:safety-hooks] continue?",
    "  1. Yes\n  2. No",
])
def test_check_safety_hooks_confirmation_detects(tmp_paths: Paths, pane):
    with patch.object(gw_watchdog, "session_alive", return_value=True), \
         patch.object(gw_watchdog, "_capture_pane", return_value=pane), \
         patch.object(gw_watchdog, "_send_keys") as send, \
         patch("time.sleep"):
        result = gw_watchdog.check_safety_hooks_confirmation(paths=tmp_paths, now=2000)
    assert result is True
    # Two sends: "1" then Enter
    assert send.call_count == 2


def test_check_safety_hooks_rate_limited(tmp_paths: Paths):
    pane = "Do you want to proceed?\n  1. Yes\n"
    marker = tmp_paths.state / "last_safety_hook_intervention"
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("2000")
    with patch.object(gw_watchdog, "session_alive", return_value=True), \
         patch.object(gw_watchdog, "_capture_pane", return_value=pane), \
         patch.object(gw_watchdog, "_send_keys") as send:
        # 5s later — under the 10s rate limit
        result = gw_watchdog.check_safety_hooks_confirmation(paths=tmp_paths, now=2005)
    assert result is False
    assert send.call_count == 0


# ---------------------------------------------------------------------------
# Daemon: must NOT exit on a single iteration error (the bash bug)
# ---------------------------------------------------------------------------

def test_run_daemon_continues_on_poll_error(tmp_paths: Paths):
    iterations = {"n": 0}

    def stop():
        iterations["n"] += 1
        return iterations["n"] > 3

    def bad_poll():
        raise RuntimeError("simulated transient telegram failure")

    sleeps: list[float] = []

    def fake_sleep(s):
        sleeps.append(s)

    with patch.object(gw_daemon, "ensure_session"), \
         patch.object(gw_daemon, "run_watchdog") as wd:
        gw_daemon.run_daemon(
            tmp_paths,
            poll_interval=0.01,
            watchdog_interval=0.0,
            stop=stop,
            poll_fn=bad_poll,
            sleep_fn=fake_sleep,
            time_fn=lambda: 0.0,
        )

    # Daemon completed all 3 iterations without exiting on poll error
    assert iterations["n"] == 4
    assert len(sleeps) == 3
    # Watchdog ran each iteration (interval=0)
    assert wd.call_count >= 1


def test_run_daemon_honors_watchdog_interval(tmp_paths: Paths):
    iterations = {"n": 0}
    times = iter([0.0, 1.0, 2.0, 3.0, 4.0, 10.0, 11.0])

    def stop():
        iterations["n"] += 1
        return iterations["n"] > 6

    with patch.object(gw_daemon, "ensure_session"), \
         patch.object(gw_daemon, "run_watchdog") as wd:
        gw_daemon.run_daemon(
            tmp_paths,
            poll_interval=0.01,
            watchdog_interval=5.0,
            stop=stop,
            poll_fn=lambda: 0,
            sleep_fn=lambda s: None,
            time_fn=lambda: next(times),
        )

    # First call (t=0) and the call after t crossed 5s (t=10) — at most 2 invocations
    assert wd.call_count == 2
