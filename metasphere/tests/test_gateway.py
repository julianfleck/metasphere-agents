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
    "[plugin:safety-hooks] continue?\n  1. Yes\n  2. No",
    "Do you want to proceed?\nfoo\n  1. Yes\n",
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


def test_safety_hooks_ignores_prose_listing(tmp_paths: Paths):
    """A list-only pane without a confirm-class line must not trigger the
    watchdog."""
    pane = "Here are options for you:\n  1. Yes — go ahead\n  2. No — abort\n"
    with patch.object(gw_watchdog, "session_alive", return_value=True), \
         patch.object(gw_watchdog, "_capture_pane", return_value=pane), \
         patch.object(gw_watchdog, "_send_keys") as send:
        result = gw_watchdog.check_safety_hooks_confirmation(paths=tmp_paths, now=2000)
    assert result is False
    assert send.call_count == 0


# ---------------------------------------------------------------------------
# Daemon: must NOT exit on a single iteration error
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


# ---------------------------------------------------------------------------
# _poll_once — end-to-end attachment routing through the shared handler
#
# Regression for 2026-04-14T21:21Z: gateway/daemon.py had its own
# per-update loop that filtered on ``if u.text and u.chat_id is not
# None``, so photos (text-less with caption) were silently dropped.
# The shared ``telegram.handler.handle_update`` now owns the full flow;
# these tests prove _poll_once actually exercises it end-to-end.
# ---------------------------------------------------------------------------

import json as _json
from metasphere.telegram import api as _tg_api, attachments as _atts, inject as _tg_inject, poller as _tg_poller


def test_poll_once_routes_photo_through_shared_handler(tmp_path, monkeypatch):
    """A photo update arrives via getUpdates; _poll_once must:
    1. Parse the photo attachment (was dropped by the old filter).
    2. Download it through getFile + http.
    3. Inject the rendered [attachments] block into tmux.
    """
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TEST:TOKEN")
    # Sandbox real paths the handler touches.
    monkeypatch.setattr(_atts, "ATTACHMENTS_ROOT", tmp_path / "attachments")
    monkeypatch.setattr(_atts, "DEBUG_LOG_PATH", tmp_path / "debug.log")

    # Fake getUpdates → one photo payload.
    photo_update = {
        "update_id": 9000,
        "message": {
            "message_id": 777,
            "chat": {"id": 42, "is_forum": False},
            "from": {"username": "julian"},
            "date": 1700000000,
            "caption": "debug-photo-1",
            "photo": [{"file_id": "p1", "file_size": 50}],
        },
    }
    monkeypatch.setattr(
        _tg_poller, "get_updates",
        lambda offset=0, timeout=30: [_tg_poller.Update.from_payload(photo_update)],
    )
    monkeypatch.setattr(_tg_poller, "load_offset", lambda path=None: 0)
    monkeypatch.setattr(_tg_poller, "save_offset", lambda offset, path=None: None)

    # Stub the api + http layers.
    http_calls: list = []

    def fake_http_get(url, timeout):
        http_calls.append(url)
        return b"BYTES:photo"

    monkeypatch.setattr(_atts, "_http_get_default", fake_http_get)

    def fake_api_call(method, **params):
        if method == "getFile":
            return {"ok": True, "result": {"file_path": f"photos/{params['file_id']}.jpg"}}
        if method == "setMessageReaction":
            return {"ok": True, "result": True}
        raise AssertionError(f"unexpected api.call: {method}")

    monkeypatch.setattr(_tg_api, "call", fake_api_call)

    # Capture tmux injection.
    tmux_calls: list = []
    monkeypatch.setattr(
        _tg_inject, "submit_to_tmux",
        lambda from_user, text, session="metasphere-orchestrator":
            tmux_calls.append({"from": from_user, "text": text}) or True,
    )

    # Redirect archiver and pending-ack writes off the real home dir.
    from metasphere.telegram import archiver as _arch
    from metasphere.telegram import handler as _handler
    monkeypatch.setattr(_arch, "DEFAULT_DIR", str(tmp_path / "tg"))
    monkeypatch.setattr(_handler, "_default_save_chat_id", lambda cid: None)
    monkeypatch.setattr(_handler, "_default_pending_ack_writer", lambda cid, mid: None)

    processed = gw_daemon._poll_once(timeout=1)

    assert processed == 1
    # Photo was downloaded through the shared handler (old gateway loop
    # would never have called getFile).
    assert len(http_calls) == 1
    assert "p1" in http_calls[0]
    # Exactly one tmux submit — with caption + [attachments] block.
    assert len(tmux_calls) == 1
    payload = tmux_calls[0]["text"]
    assert payload.startswith("debug-photo-1")
    assert "[attachments]" in payload
    assert str(tmp_path / "attachments" / "777") in payload


def test_poll_once_does_not_drop_photo_only_messages(tmp_path, monkeypatch):
    """Regression for the exact production bug: a photo with no text
    and no caption used to be silently skipped by the old
    ``if u.text and u.chat_id is not None`` filter. The shared handler
    must inject a pure [attachments] block instead.
    """
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "TEST:TOKEN")
    monkeypatch.setattr(_atts, "ATTACHMENTS_ROOT", tmp_path / "attachments")
    monkeypatch.setattr(_atts, "DEBUG_LOG_PATH", tmp_path / "debug.log")

    bare_photo = {
        "update_id": 9001,
        "message": {
            "message_id": 778,
            "chat": {"id": 42, "is_forum": False},
            "from": {"username": "julian"},
            "date": 1700000000,
            "photo": [{"file_id": "p2", "file_size": 50}],
        },
    }
    monkeypatch.setattr(
        _tg_poller, "get_updates",
        lambda offset=0, timeout=30: [_tg_poller.Update.from_payload(bare_photo)],
    )
    monkeypatch.setattr(_tg_poller, "load_offset", lambda path=None: 0)
    monkeypatch.setattr(_tg_poller, "save_offset", lambda offset, path=None: None)

    monkeypatch.setattr(_atts, "_http_get_default", lambda url, t: b"BYTES:photo")

    def fake_call(method, **params):
        if method == "getFile":
            return {"ok": True, "result": {"file_path": "photos/p2.jpg"}}
        if method == "setMessageReaction":
            return {"ok": True, "result": True}
        raise AssertionError(method)

    monkeypatch.setattr(_tg_api, "call", fake_call)

    tmux_calls: list = []
    monkeypatch.setattr(
        _tg_inject, "submit_to_tmux",
        lambda fu, t, session="metasphere-orchestrator":
            tmux_calls.append({"from": fu, "text": t}) or True,
    )

    from metasphere.telegram import archiver as _arch
    from metasphere.telegram import handler as _handler
    monkeypatch.setattr(_arch, "DEFAULT_DIR", str(tmp_path / "tg"))
    monkeypatch.setattr(_handler, "_default_save_chat_id", lambda cid: None)
    monkeypatch.setattr(_handler, "_default_pending_ack_writer", lambda cid, mid: None)

    gw_daemon._poll_once(timeout=1)

    assert len(tmux_calls) == 1
    payload = tmux_calls[0]["text"]
    assert payload.startswith("[attachments]")
    assert "photo" in payload


def test_poll_once_handler_exception_does_not_block_offset_advance(tmp_path, monkeypatch):
    """If handle_update raises for one update, _poll_once must still
    advance the offset for that update (so we don't re-process it
    forever) and return a valid count.
    """
    fake_updates = [
        _tg_poller.Update.from_payload({
            "update_id": 9002,
            "message": {
                "message_id": 800,
                "chat": {"id": 1, "is_forum": False},
                "from": {"username": "x"},
                "text": "hi",
            },
        }),
    ]
    monkeypatch.setattr(_tg_poller, "get_updates",
                         lambda offset=0, timeout=30: fake_updates)
    monkeypatch.setattr(_tg_poller, "load_offset", lambda path=None: 0)

    saved_offsets: list = []
    monkeypatch.setattr(_tg_poller, "save_offset",
                         lambda o, path=None: saved_offsets.append(o))

    from metasphere.telegram import handler as _handler
    def boom(u, **_k):
        raise RuntimeError("simulated handler failure")
    monkeypatch.setattr(_handler, "handle_update", boom)

    processed = gw_daemon._poll_once(timeout=1)

    assert processed == 1
    # Offset advanced past the failing update so we don't re-drive it.
    assert saved_offsets == [9003]


# ---------------------------------------------------------------------------
# restart_agent_session — /exit Enter-race fix (PR #18)
#
# 2026-04-16: supervisor.restart_claude fired but the tmux pane never
# cycled — the single ``send-keys /exit Enter`` invocation races the
# REPL's input-state machine post-C-c. Prior art in ``metasphere.tmux
# .submit_to_tmux`` separates the literal-text send from the Enter,
# with settles between. These tests pin the fixed sequence so a
# future refactor can't regress it.
# ---------------------------------------------------------------------------


def test_restart_claude_uses_separated_exit_and_enter_sequence(tmp_paths, monkeypatch):
    """The fix: ``/exit`` is sent via ``send-keys -l --`` (literal) as
    its own call, then Enter is a separate send-keys. Double Enter as
    belt-and-suspenders. No single ``/exit Enter`` invocation.
    """
    calls: list[list[str]] = []

    def fake_tmux(*args: str):
        calls.append(list(args))
        cp = MagicMock()
        cp.returncode = 0
        cp.stdout = ""
        cp.stderr = ""
        return cp

    monkeypatch.setattr(gw_session, "_tmux", fake_tmux)
    monkeypatch.setattr(gw_session, "session_alive", lambda name=None: True)
    # _write_restart_pending writes under paths.state; tmp_paths
    # already redirects that.
    ok = gw_session.restart_agent_session(
        agent="@orchestrator", reason="test", paths=tmp_paths,
    )
    assert ok is True

    # Sequence matches the post-fix shape:
    #   C-c, C-c, C-u, send-keys -l -- /exit, Enter, Enter
    sendkeys = [c for c in calls if c and c[0] == "send-keys"]
    # 5+ send-keys calls (2x C-c, 1x C-u, 1x /exit, 2x Enter).
    assert len(sendkeys) >= 6

    # No single call that contains BOTH "/exit" AND "Enter" — that
    # combined form is exactly the race we're fixing.
    for call in sendkeys:
        if "/exit" in call:
            assert "Enter" not in call, (
                f"/exit must not share a send-keys call with Enter: {call}"
            )

    # /exit sent via literal mode.
    exit_calls = [c for c in sendkeys if "/exit" in c]
    assert len(exit_calls) == 1
    assert "-l" in exit_calls[0], f"/exit must use -l: {exit_calls[0]}"

    # At least two separate Enter sends after the /exit.
    exit_idx = sendkeys.index(exit_calls[0])
    enters_after = [c for c in sendkeys[exit_idx + 1:] if c[-1] == "Enter"]
    assert len(enters_after) >= 2, (
        f"expected belt-and-suspenders double-Enter, got {enters_after}"
    )


def test_restart_claude_sends_c_c_twice_before_exit(tmp_paths, monkeypatch):
    """C-c × 2 with settles precedes /exit — kills any in-flight tool
    call / input buffer before issuing the slash command. Pre-fix
    this was already correct; test pins it.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(gw_session, "_tmux",
                         lambda *a: (calls.append(list(a)) or MagicMock(
                             returncode=0, stdout="", stderr="")))
    monkeypatch.setattr(gw_session, "session_alive", lambda name=None: True)

    gw_session.restart_agent_session(
        agent="@orchestrator", reason="test", paths=tmp_paths,
    )

    sendkeys = [c for c in calls if c and c[0] == "send-keys"]
    c_cs = [c for c in sendkeys if "C-c" in c]
    assert len(c_cs) == 2, f"expected 2x C-c before /exit, got {len(c_cs)}"

    # C-c comes before /exit.
    exit_idx = next(i for i, c in enumerate(sendkeys) if "/exit" in c)
    c_c_idxs = [i for i, c in enumerate(sendkeys) if "C-c" in c]
    for idx in c_c_idxs:
        assert idx < exit_idx, "C-c must precede /exit"


def test_restart_claude_skips_when_session_dead(tmp_paths, monkeypatch):
    """No tmux traffic + return False when the session isn't alive.
    Fast-path for first-boot / crashed-pane states.
    """
    calls: list[list[str]] = []
    monkeypatch.setattr(gw_session, "_tmux",
                         lambda *a: (calls.append(list(a)) or MagicMock(
                             returncode=0, stdout="", stderr="")))
    monkeypatch.setattr(gw_session, "session_alive", lambda name=None: False)
    ok = gw_session.restart_agent_session(
        agent="@orchestrator", reason="test", paths=tmp_paths,
    )
    assert ok is False
    # No send-keys traffic.
    sendkeys = [c for c in calls if c and c[0] == "send-keys"]
    assert sendkeys == []
