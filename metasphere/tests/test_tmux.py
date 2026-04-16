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


# --- Layer 1: attach-aware injection ---------------------------------------
#
# 2026-04-16: Julian was typing into the attached orchestrator pane while a
# heartbeat fired ``submit_to_tmux``; the heartbeat's send-keys interleaved
# with his keystrokes and submitted the garbled mess. A fcntl lock cannot
# help here — human keystrokes go via the tty, bypassing in-process locks.
# Fix is upstream: detect attachment, defer auto-injections.


def test_pane_has_human_client_true_when_clients_listed(monkeypatch):
    def fake_run(argv, **kw):
        if "list-clients" in argv:
            return _fake_cp(stdout="/dev/pts/8\n")
        return _fake_cp(returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(T, "_find_tmux", lambda: "/usr/bin/tmux")
    assert T.pane_has_human_client("sess") is True


def test_pane_has_human_client_false_when_no_clients(monkeypatch):
    def fake_run(argv, **kw):
        if "list-clients" in argv:
            return _fake_cp(stdout="")
        return _fake_cp(returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(T, "_find_tmux", lambda: "/usr/bin/tmux")
    assert T.pane_has_human_client("sess") is False


def test_pane_has_human_client_fails_open_on_tmux_error(monkeypatch):
    """If tmux list-clients errors, return False (proceed). Better to
    occasionally interleave than to silently drop every heartbeat when
    tmux misbehaves."""
    def fake_run(argv, **kw):
        if "list-clients" in argv:
            return _fake_cp(returncode=1, stdout="")
        return _fake_cp(returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(T, "_find_tmux", lambda: "/usr/bin/tmux")
    assert T.pane_has_human_client("sess") is False


def test_input_line_has_typing_detects_typed_content(monkeypatch):
    def fake_run(argv, **kw):
        if "capture-pane" in argv:
            return _fake_cp(stdout="some output\n> the orchest\n")
        return _fake_cp(returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)
    assert T._input_line_has_typing("/usr/bin/tmux", "sess") is True


def test_input_line_has_typing_false_for_bare_prompt(monkeypatch):
    def fake_run(argv, **kw):
        if "capture-pane" in argv:
            return _fake_cp(stdout="some output\n>\n")
        return _fake_cp(returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)
    assert T._input_line_has_typing("/usr/bin/tmux", "sess") is False


def test_input_line_has_typing_false_for_paste_placeholder(monkeypatch):
    """A pre-existing ``[Pasted text #`` placeholder is something the
    Escape×2 pre-clear handles — not typing. Must not abort."""
    def fake_run(argv, **kw):
        if "capture-pane" in argv:
            return _fake_cp(stdout="> [Pasted text #4 +18 lines]\n")
        return _fake_cp(returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)
    assert T._input_line_has_typing("/usr/bin/tmux", "sess") is False


def test_input_line_has_typing_strips_box_drawing_chars(monkeypatch):
    """Claude TUI renders the input box with ``│`` borders; the prompt
    detection must look inside the box, not at the border."""
    def fake_run(argv, **kw):
        if "capture-pane" in argv:
            return _fake_cp(stdout="│ > some typing │\n")
        return _fake_cp(returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)
    assert T._input_line_has_typing("/usr/bin/tmux", "sess") is True


def test_submit_defer_if_busy_skips_when_human_attached(monkeypatch):
    """When ``defer_if_busy=True`` and a tty client is attached, abort
    BEFORE firing any send-keys (no Escape, no typing, no Enter)."""
    def fake_run(argv, **kw):
        if "has-session" in argv:
            return _fake_cp(returncode=0)
        if "list-clients" in argv:
            return _fake_cp(stdout="/dev/pts/8\n")
        if "capture-pane" in argv:
            return _fake_cp(stdout="")
        return _fake_cp(returncode=0)

    calls: list[list[str]] = []
    orig = fake_run

    def recording(argv, **kw):
        calls.append(list(argv))
        return orig(argv, **kw)

    monkeypatch.setattr("subprocess.run", recording)
    monkeypatch.setattr(T, "_find_tmux", lambda: "/usr/bin/tmux")
    assert T.submit_to_tmux("sess", "hello", defer_if_busy=True) is False

    sendkeys = [c for c in calls if "send-keys" in c]
    assert sendkeys == [], (
        f"deferred submit must not fire any send-keys, got: {sendkeys}"
    )


def test_submit_defer_if_busy_skips_when_input_has_typing(monkeypatch):
    """When ``defer_if_busy=True`` and the input area shows typed
    content, abort BEFORE firing any send-keys."""
    def fake_run(argv, **kw):
        if "has-session" in argv:
            return _fake_cp(returncode=0)
        if "list-clients" in argv:
            return _fake_cp(stdout="")
        if "capture-pane" in argv:
            return _fake_cp(stdout="> mid-typing-text\n")
        return _fake_cp(returncode=0)

    calls: list[list[str]] = []

    def recording(argv, **kw):
        calls.append(list(argv))
        return fake_run(argv, **kw)

    monkeypatch.setattr("subprocess.run", recording)
    monkeypatch.setattr(T, "_find_tmux", lambda: "/usr/bin/tmux")
    assert T.submit_to_tmux("sess", "hello", defer_if_busy=True) is False

    sendkeys = [c for c in calls if "send-keys" in c]
    assert sendkeys == [], (
        f"deferred submit must not fire any send-keys, got: {sendkeys}"
    )


def test_submit_defer_if_busy_proceeds_when_clean(monkeypatch):
    """When ``defer_if_busy=True`` but no client and no typing, the
    submit proceeds normally (Escape×2, type, Enter)."""
    calls = _capture_calls(monkeypatch)
    assert T.submit_to_tmux("sess", "hello", defer_if_busy=True) is True
    sendkeys = [c for c in calls if "send-keys" in c]
    assert sendkeys, "expected send-keys to fire on clean pane"
    assert any("-l" in c for c in sendkeys), "expected literal typing"


def test_submit_defer_if_busy_default_false_ignores_attach(monkeypatch):
    """Manual CLI paths use the default ``defer_if_busy=False``; these
    must NOT skip when a client happens to be attached."""
    def fake_run(argv, **kw):
        if "has-session" in argv:
            return _fake_cp(returncode=0)
        if "list-clients" in argv:
            return _fake_cp(stdout="/dev/pts/8\n")
        if "capture-pane" in argv:
            return _fake_cp(stdout="> mid-typing\n")
        return _fake_cp(returncode=0)

    calls: list[list[str]] = []

    def recording(argv, **kw):
        calls.append(list(argv))
        return fake_run(argv, **kw)

    monkeypatch.setattr("subprocess.run", recording)
    monkeypatch.setattr(T, "_find_tmux", lambda: "/usr/bin/tmux")
    # Default — no defer. Must proceed despite the attach.
    assert T.submit_to_tmux("sess", "hello") is True
    sendkeys = [c for c in calls if "send-keys" in c]
    assert sendkeys, "manual submit must not be skipped by attach detection"


