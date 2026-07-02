"""Tests for the wake-banner truncation guard (B1).

When a wake ``first_task`` body exceeds the threshold, ``wake_persistent``
persists the full body as a ``!task`` message in the recipient's inbox
and injects only a short pointer banner — bypassing the Claude Code TUI's
bracketed-paste cap that produced the repro case on 2026-05-29.
"""

from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from metasphere import agents
from metasphere.paths import Paths


def _make_persistent(tmp_paths: Paths, name: str = "@waker") -> Path:
    d = tmp_paths.agents / name
    d.mkdir(parents=True)
    (d / "MISSION.md").write_text("mission")
    (d / "scope").write_text(str(tmp_paths.project_root))
    return d


# ---------------------------------------------------------------------------
# Unit tests on the banner-preparation helper.
# ---------------------------------------------------------------------------


def test_short_body_returns_inline_banner(tmp_paths: Paths):
    _make_persistent(tmp_paths)
    banner = agents._prepare_wake_banner(
        "@waker", "short task body", tmp_paths,
    )
    assert banner == "[task] short task body"


def test_threshold_boundary_still_inline(tmp_paths: Paths):
    # At exactly the threshold (inclusive lower-bound), still inline.
    _make_persistent(tmp_paths)
    body = "a" * agents._WAKE_BANNER_BODY_THRESHOLD
    banner = agents._prepare_wake_banner("@waker", body, tmp_paths)
    assert banner == f"[task] {body}"


def test_long_body_persists_and_returns_pointer(tmp_paths: Paths):
    _make_persistent(tmp_paths)
    long_body = "x" * (agents._WAKE_BANNER_BODY_THRESHOLD + 100)

    banner = agents._prepare_wake_banner("@waker", long_body, tmp_paths)

    assert banner.startswith("[task] Long body persisted as msg-")
    assert "metasphere msg read" in banner
    # Pointer banner is well under the threshold so it can't itself
    # re-trigger truncation.
    assert len(banner.encode("utf-8")) < agents._WAKE_BANNER_BODY_THRESHOLD

    # The full body must be readable from the recipient inbox by id.
    from metasphere import messages as _msgs
    msgs = _msgs.collect_inbox(tmp_paths.scope, tmp_paths.project_root)
    found = [m for m in msgs if m.label == "!task" and long_body in m.body]
    assert found, "expected the long body to be persisted as a !task"


def test_send_failure_falls_back_to_inline(tmp_paths: Paths):
    # If the message-send surface raises (disk full, permissions, etc.)
    # we still attempt the wake inline — losing the wake is strictly
    # worse than re-risking truncation.
    _make_persistent(tmp_paths)
    long_body = "y" * (agents._WAKE_BANNER_BODY_THRESHOLD + 100)
    with patch(
        "metasphere.messages.send_message",
        side_effect=RuntimeError("disk full"),
    ):
        banner = agents._prepare_wake_banner(
            "@waker", long_body, tmp_paths,
        )
    assert banner == f"[task] {long_body}"


# ---------------------------------------------------------------------------
# wake_persistent integration — confirms the helper is wired into the
# warm-session inject site (the path today's repro hit).
# ---------------------------------------------------------------------------


def test_wake_persistent_warm_uses_pointer_for_long_body(tmp_paths: Paths):
    _make_persistent(tmp_paths)
    long_body = "z" * (agents._WAKE_BANNER_BODY_THRESHOLD + 200)
    captured: list[tuple[str, str]] = []

    def fake_tmux_submit(session, body, **kwargs):
        captured.append((session, body))
        return True

    def fake_run(cmd, *_, **__):
        cp = MagicMock()
        if "has-session" in cmd:
            cp.returncode = 0  # session alive → warm branch
        elif "display-message" in cmd:
            cp.returncode = 0
            cp.stdout = str(int(time.time()))  # fresh idle
        else:
            cp.returncode = 0
            cp.stdout = ""
        cp.stderr = ""
        return cp

    with patch("metasphere.agents.subprocess.run", side_effect=fake_run), \
            patch("metasphere.agents._tmux_submit",
                  side_effect=fake_tmux_submit):
        rec, delivered = agents.wake_persistent(
            "@waker", first_task=long_body, paths=tmp_paths,
        )

    assert delivered is True
    assert captured, "expected _tmux_submit to be invoked"
    _, body = captured[0]
    assert body.startswith("[task] Long body persisted as msg-")
    assert long_body not in body  # 5KB body must not hit tmux


def test_wake_persistent_warm_inline_short_body(tmp_paths: Paths):
    _make_persistent(tmp_paths)
    short_body = "ship it"
    captured: list[str] = []

    def fake_tmux_submit(session, body, **kwargs):
        captured.append(body)
        return True

    def fake_run(cmd, *_, **__):
        cp = MagicMock()
        if "has-session" in cmd:
            cp.returncode = 0
        elif "display-message" in cmd:
            cp.returncode = 0
            cp.stdout = str(int(time.time()))
        else:
            cp.returncode = 0
            cp.stdout = ""
        cp.stderr = ""
        return cp

    with patch("metasphere.agents.subprocess.run", side_effect=fake_run), \
            patch("metasphere.agents._tmux_submit",
                  side_effect=fake_tmux_submit):
        agents.wake_persistent(
            "@waker", first_task=short_body, paths=tmp_paths,
        )

    assert captured == ["[task] ship it"]


# ---------------------------------------------------------------------------
# Real-tmux integration — exercises the actual wake-delivery surface
# end-to-end with no subprocess mocking. The pre-created session runs
# ``cat`` so paste content is observable via ``tmux capture-pane``.
# Skipped (not failed) when tmux is unavailable per the brief.
# ---------------------------------------------------------------------------


_TMUX = shutil.which("tmux")


@pytest.mark.skipif(_TMUX is None, reason="tmux not available")
def test_real_tmux_long_body_pointer_lands_in_pane(tmp_paths: Paths):
    """End-to-end on a real tmux session: a 5KB body must reach the
    inbox as a ``!task`` and the pane must show only the pointer
    banner — never the raw body that triggered the truncation."""
    _make_persistent(tmp_paths, "@waker")
    session = agents.session_name_for("@waker")
    # Force-kill in case a prior aborted run left it.
    subprocess.run(
        [_TMUX, "kill-session", "-t", session],
        check=False,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    subprocess.run(
        [_TMUX, "new-session", "-d", "-s", session, "cat"],
        check=True,
    )
    try:
        long_body = "Z" * 5000
        rec, _delivered = agents.wake_persistent(
            "@waker", first_task=long_body, paths=tmp_paths,
        )

        # Allow ``submit_to_tmux``'s poll loop to settle. The pointer
        # banner is small so the paste-poll exits fast on a cat session.
        time.sleep(2)

        cap = subprocess.run(
            [_TMUX, "capture-pane", "-p", "-t", session],
            capture_output=True, text=True, check=False,
        )
        pane = cap.stdout
        assert "[task] Long body persisted as msg-" in pane, pane
        # The 5KB Z-run must NOT appear in the pane — that is the proof
        # the truncation surface was bypassed entirely.
        assert "Z" * 200 not in pane

        # And the full body lives in the recipient's inbox.
        from metasphere import messages as _msgs
        msgs = _msgs.collect_inbox(
            tmp_paths.scope, tmp_paths.project_root,
        )
        found = [
            m for m in msgs if m.label == "!task" and long_body in m.body
        ]
        assert found, "expected the long body in the recipient inbox"
    finally:
        subprocess.run(
            [_TMUX, "kill-session", "-t", session],
            check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
