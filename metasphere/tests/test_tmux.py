"""Tests for ``metasphere.tmux.submit_to_tmux``.

The 2026-04-16 research-monitor outage was caused by stuck
``[Pasted text #N`` placeholders accumulating in agent panes: each
cron-fired wake added another paste on top of the previous unsubmitted
one, the Enter-retry loop saw "placeholder still present" and gave up
without clearing, and the next wake compounded the problem.

This module pins the fix: pre-emptive Escape×2 on every submit to
clear any pending paste before laying down a new one, plus a final
Escape if the Enter-retry loop exhausts without clearing.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from metasphere import tmux as T


def _fake_cp(returncode: int = 0, stdout: str = ""):
    cp = MagicMock()
    cp.returncode = returncode
    cp.stdout = stdout
    cp.stderr = ""
    return cp


def _capture_calls(monkeypatch, pane_states=None):
    """Monkeypatch subprocess.run to record every tmux call. ``pane_states``
    is a list of strings returned by successive ``capture-pane`` calls;
    the list is consumed one entry per capture-pane invocation (default
    behavior: always clean pane).
    """
    pane_states = list(pane_states or [])
    calls: list[list[str]] = []

    def fake_run(argv, **kw):
        calls.append(list(argv))
        if "has-session" in argv:
            return _fake_cp(returncode=0)
        if "capture-pane" in argv:
            stdout = pane_states.pop(0) if pane_states else ""
            return _fake_cp(stdout=stdout)
        return _fake_cp(returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(T, "_find_tmux", lambda: "/usr/bin/tmux")
    return calls


def test_submit_prefixes_escape_escape_before_typing(monkeypatch):
    """Every submit starts with ``Escape Escape`` to clear any stacked
    paste placeholder from a prior wake.
    """
    calls = _capture_calls(monkeypatch)
    assert T.submit_to_tmux("sess", "hello") is True

    sendkeys = [c for c in calls if "send-keys" in c]
    # First send-keys call must be the Escape×2 pre-clear.
    first = sendkeys[0]
    assert "Escape" in first
    # Must precede any literal-text typing.
    literal_idx = next((i for i, c in enumerate(sendkeys) if "-l" in c), -1)
    assert literal_idx > 0
    assert sendkeys[0] is sendkeys[literal_idx - 1] or literal_idx > 0


def test_submit_typing_sequence_unchanged_after_prefix(monkeypatch):
    """Body-typing behavior (``-l -- line``, ``C-j`` between lines,
    settle then Enter) is preserved — only the Escape×2 prefix is new.
    """
    calls = _capture_calls(monkeypatch)
    T.submit_to_tmux("sess", "line1\nline2")
    sendkeys = [c for c in calls if "send-keys" in c]
    # First: Escape prefix.
    assert "Escape" in sendkeys[0]
    # Then literal typing for "line1", then C-j, then literal "line2".
    types = [c for c in sendkeys if "-l" in c]
    assert any("line1" in c for c in types)
    assert any("line2" in c for c in types)
    assert any("C-j" in c for c in sendkeys)
    # Final Enter exists.
    assert any(c[-1] == "Enter" for c in sendkeys)


def test_submit_stuck_paste_retries_then_escape_fallback(monkeypatch):
    """If the Enter-retry loop exhausts with the placeholder still
    visible, submit_to_tmux fires Escape×2 as a last-ditch cleanup
    rather than silently returning False with a stacked placeholder
    left behind. Prevents the 2026-04-16 accumulation pattern.
    """
    # capture-pane consumption order:
    #   1-3. retry-loop checks × 3 (all show placeholder)
    #   4. pre-escape check inside `if _has_pending_paste` guard
    #   5. post-escape final check (clean)
    pane_states = [
        "[Pasted text #4 +18 lines]",     # retry iter 1
        "[Pasted text #4 +18 lines]",     # retry iter 2
        "[Pasted text #4 +18 lines]",     # retry iter 3
        "[Pasted text #4 +18 lines]",     # pre-escape check inside condition
        "",                                # post-escape final check → clean
    ]
    calls = _capture_calls(monkeypatch, pane_states=pane_states)
    T.submit_to_tmux("sess", "m")

    sendkeys = [c for c in calls if "send-keys" in c]
    escapes = [c for c in sendkeys if "Escape" in c]
    # Two Escape send-keys calls: one at the start, one at the end.
    assert len(escapes) >= 2, (
        f"expected a prefix Escape and a fallback Escape, got {escapes}"
    )


def test_submit_returns_false_when_session_missing(monkeypatch):
    """Pre-existing invariant — no tmux traffic when the session is
    dead. Pinning so the Escape prefix doesn't regress this.
    """
    def fake_run(argv, **kw):
        if "has-session" in argv:
            return _fake_cp(returncode=1)  # dead
        return _fake_cp()

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(T, "_find_tmux", lambda: "/usr/bin/tmux")
    assert T.submit_to_tmux("sess", "hello") is False


def test_submit_to_tmux_never_raises(monkeypatch):
    def boom(*a, **kw):
        raise OSError("simulated")

    monkeypatch.setattr("subprocess.run", boom)
    monkeypatch.setattr(T, "_find_tmux", lambda: "/usr/bin/tmux")
    # Must return False, not raise.
    assert T.submit_to_tmux("sess", "hello") is False


