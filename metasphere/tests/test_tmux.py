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


def test_submit_prefixes_single_escape_before_typing(monkeypatch):
    """Every user-inbound submit starts with a SINGLE Escape to interrupt
    any running Claude Code turn. Esc Esc would open Claude Code's
    Rewind/Undo menu (not clear input), which is what the 2026-04-16
    telegram-inbound outage was: we typed into the rewind menu's filter
    and Enter rolled back to a random prior turn. Exactly one Escape.
    """
    calls = _capture_calls(monkeypatch)
    assert T.submit_to_tmux("sess", "hello") is True

    sendkeys = [c for c in calls if "send-keys" in c]
    escapes = [c for c in sendkeys if "Escape" in c]
    # Exactly one Escape send-keys call — the pre-type interrupt.
    # Never two (that's the rewind menu keybinding).
    assert len(escapes) == 1, (
        f"expected exactly ONE Escape (single Escape = interrupt), got {escapes}"
    )
    # First call must be the Escape (before any typing).
    assert "Escape" in sendkeys[0]
    # Must precede any literal-text typing.
    literal_idx = next((i for i, c in enumerate(sendkeys) if "-l" in c), -1)
    assert literal_idx > 0


def test_submit_typing_sequence_unchanged_after_prefix(monkeypatch):
    """Body-typing behavior (``-l -- line``, ``C-j`` between lines,
    settle then Enter) is preserved — only the Escape prefix is new.
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
    # Final submit uses C-m (ASCII 0x0D), NOT the Enter keysym.
    assert any(c[-1] == "C-m" for c in sendkeys)


def test_submit_uses_c_m_not_enter_keysym(monkeypatch):
    """Submit must use ``C-m`` (ASCII 0x0D) not the ``Enter`` keysym.

    2026-04-20 root cause: tmux 3.3a's ``Enter`` keysym was silently
    dropped by Claude Code's TUI input handler (Ink/React). Two
    wake-Enter races in one session (visa-lead, metasphere-lead) were
    unblocked only by ``C-m``. The ``Enter`` keysym works fine for bash
    readline but NOT for Claude Code's prompt-submit path.

    Regression guard: if someone casually "cleans up" C-m back to Enter,
    this test fails.
    """
    calls = _capture_calls(monkeypatch)
    T.submit_to_tmux("sess", "probe")
    sendkeys = [c for c in calls if "send-keys" in c]
    # All submit-like calls must use C-m, never the Enter keysym.
    for c in sendkeys:
        assert "Enter" not in c, (
            f"send-keys must use 'C-m' not 'Enter' keysym "
            f"(2026-04-20 wake-Enter race). Got: {c}"
        )
    # At least one C-m call exists (the submit).
    assert any("C-m" in c for c in sendkeys), (
        "expected at least one C-m send-keys call for submit"
    )


def test_submit_no_fallback_escape_on_retry_exhaust(monkeypatch):
    """If the Enter-retry loop exhausts with the input still dirty,
    submit_to_tmux must NOT fire a fallback Escape. Old behavior was
    Escape×2 (which opened the rewind menu — 2026-04-16 telegram
    outage); a single Escape here would interrupt the turn we just
    submitted; leaving the typed text lets submit_watchdog clean up
    asynchronously. Only one Escape send-keys call should happen
    regardless of whether retries succeed.
    """
    pane_states = [
        "[Pasted text #4 +18 lines]",     # retry iter 1 dirty
        "[Pasted text #4 +18 lines]",     # retry iter 2 dirty
        "[Pasted text #4 +18 lines]",     # retry iter 3 dirty
        "[Pasted text #4 +18 lines]",     # final return check still dirty
    ]
    calls = _capture_calls(monkeypatch, pane_states=pane_states)
    T.submit_to_tmux("sess", "m")

    sendkeys = [c for c in calls if "send-keys" in c]
    escapes = [c for c in sendkeys if "Escape" in c]
    # Exactly ONE Escape (the pre-clear), never two.
    assert len(escapes) == 1, (
        f"retry-exhaust must not fire fallback Escape, got {escapes}"
    )


def test_submit_skips_escape_prefix_when_disabled(monkeypatch):
    """Auto-injectors pass ``escape_prefix=False`` so they never interrupt
    a running Claude Code tool call. The initial Escape×2 pre-clear AND
    the fallback Escape at the end must both be suppressed. "Only
    user-inbound interrupts" (Julian 2026-04-16)."""
    calls = _capture_calls(monkeypatch)
    T.submit_to_tmux("sess", "hello", escape_prefix=False)

    sendkeys = [c for c in calls if "send-keys" in c]
    escapes = [c for c in sendkeys if "Escape" in c]
    assert escapes == [], (
        f"auto-injector must not send Escape, got {escapes}"
    )
    # Still typed and Enter'd
    assert any("hello" in c for c in sendkeys)
    assert any(c[-1] == "C-m" for c in sendkeys)


def test_submit_zero_escapes_when_escape_prefix_false_and_dirty(monkeypatch):
    """Retry-loop exhaustion with a stuck placeholder: auto-injector
    (``escape_prefix=False``) must emit ZERO Escape send-keys. The
    stuck state is left for the submit_watchdog daemon to clean up
    on its next tick."""
    pane_states = [
        "[Pasted text #4 +18 lines]",     # retry iter 1
        "[Pasted text #4 +18 lines]",     # retry iter 2
        "[Pasted text #4 +18 lines]",     # retry iter 3
        "[Pasted text #4 +18 lines]",     # final return check (still dirty)
    ]
    calls = _capture_calls(monkeypatch, pane_states=pane_states)
    T.submit_to_tmux("sess", "m", escape_prefix=False)

    sendkeys = [c for c in calls if "send-keys" in c]
    escapes = [c for c in sendkeys if "Escape" in c]
    assert escapes == [], (
        f"escape_prefix=False must emit zero Escapes, got {escapes}"
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


# --- Input-buffer guard (Layer 2) ------------------------------------------
#
# 2026-04-16: Julian was typing into the attached orchestrator pane while a
# heartbeat fired ``submit_to_tmux``; the heartbeat's send-keys interleaved
# with his keystrokes and submitted the garbled mess. A fcntl lock cannot
# help here — human keystrokes go via the tty, bypassing in-process locks.
# An attach-aware guard (was PR #22) overreached because Julian keeps panes
# attached for monitoring. The correct primitive is: inspect the input box
# and defer when it shows typed content.


_BORDER = "─" * 60


def _pane(input_lines: list[str]) -> str:
    """Build a fake Claude Code pane capture with the input box borders
    wrapping the given input_lines. Matches the real layout: some tool
    output above, then top border, then the input lines, then bottom
    border, then a footer."""
    parts = ["tool output above", _BORDER]
    parts.extend(input_lines)
    parts.append(_BORDER)
    parts.append("  ⏵⏵ bypass permissions on · esc to interrupt")
    return "\n".join(parts) + "\n"


def test_input_line_has_typing_detects_typed_content(monkeypatch):
    def fake_run(argv, **kw):
        if "capture-pane" in argv:
            return _fake_cp(stdout=_pane(["❯ hello there"]))
        return _fake_cp(returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)
    assert T._input_line_has_typing("/usr/bin/tmux", "sess") is True


def test_input_line_has_typing_false_for_bare_prompt(monkeypatch):
    def fake_run(argv, **kw):
        if "capture-pane" in argv:
            return _fake_cp(stdout=_pane(["❯ "]))
        return _fake_cp(returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)
    assert T._input_line_has_typing("/usr/bin/tmux", "sess") is False


def test_input_line_has_typing_false_for_paste_placeholder(monkeypatch):
    """A pre-existing ``[Pasted text #`` placeholder is not typing —
    submit_watchdog handles those asynchronously."""
    def fake_run(argv, **kw):
        if "capture-pane" in argv:
            return _fake_cp(stdout=_pane(["❯ [Pasted text #4 +18 lines]"]))
        return _fake_cp(returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)
    assert T._input_line_has_typing("/usr/bin/tmux", "sess") is False


def test_input_line_has_typing_false_for_bare_chevron_with_nbsp(monkeypatch):
    """Empty Claude Code prompt renders as ``❯\\xa0`` (chevron + NBSP).
    ``str.strip()`` removes NBSP; must return False."""
    def fake_run(argv, **kw):
        if "capture-pane" in argv:
            return _fake_cp(stdout=_pane(["❯\xa0"]))
        return _fake_cp(returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)
    assert T._input_line_has_typing("/usr/bin/tmux", "sess") is False


def test_input_line_has_typing_detects_wrapped_multiline_input(monkeypatch):
    """2026-04-16 regression: when Julian typed a long message that
    Claude Code wrapped across multiple lines, the old heuristic (walk
    last 10 lines looking for ``❯``) missed the continuation lines and
    heartbeats fired mid-typing, interleaving with his keystrokes.

    The new border-based heuristic must see ALL content between the two
    ``─────`` borders, even when the first (``❯``) line is pushed far
    above the last 10 lines by continuation wrapping."""
    wrapped = ["❯ this is a message Julian is typing"]
    # Push the chevron line well past the last-10-window.
    wrapped += [f"  continuation line {i}" for i in range(15)]

    def fake_run(argv, **kw):
        if "capture-pane" in argv:
            return _fake_cp(stdout=_pane(wrapped))
        return _fake_cp(returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)
    assert T._input_line_has_typing("/usr/bin/tmux", "sess") is True


def test_input_line_has_typing_false_when_only_continuation_is_whitespace(monkeypatch):
    """An empty input box with a trailing blank continuation line (TUI
    quirk) should not be mistaken for typing."""
    def fake_run(argv, **kw):
        if "capture-pane" in argv:
            return _fake_cp(stdout=_pane(["❯\xa0", "", ""]))
        return _fake_cp(returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)
    assert T._input_line_has_typing("/usr/bin/tmux", "sess") is False


def test_input_line_has_typing_false_when_no_borders_visible(monkeypatch):
    """Fail open: if capture-pane doesn't show the input box (e.g.,
    alt-screen app covering the TUI), don't block heartbeats."""
    def fake_run(argv, **kw):
        if "capture-pane" in argv:
            return _fake_cp(stdout="just plain text\nno borders here\n")
        return _fake_cp(returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)
    assert T._input_line_has_typing("/usr/bin/tmux", "sess") is False


def test_input_line_has_typing_fails_open_on_error(monkeypatch):
    """Fail open on capture-pane errors — better to occasionally
    interleave than drop every heartbeat on tmux quirks."""
    def fake_run(argv, **kw):
        if "capture-pane" in argv:
            return _fake_cp(returncode=1)
        return _fake_cp(returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)
    assert T._input_line_has_typing("/usr/bin/tmux", "sess") is False


def test_submit_defer_if_busy_skips_when_input_has_typing(monkeypatch):
    """When ``defer_if_busy=True`` and the input area shows typed
    content, abort BEFORE firing any send-keys (no Escape, no typing,
    no Enter)."""
    def fake_run(argv, **kw):
        if "has-session" in argv:
            return _fake_cp(returncode=0)
        if "capture-pane" in argv:
            return _fake_cp(stdout=_pane(["❯ mid-typing-text"]))
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


def test_submit_defer_if_busy_proceeds_on_bare_prompt(monkeypatch):
    """When ``defer_if_busy=True`` but the input is empty, the submit
    proceeds normally (Escape×2, type, Enter). Crucially: a client
    being attached to watch the pane is NOT itself a reason to defer
    — Julian keeps panes attached for monitoring."""
    calls = _capture_calls(monkeypatch)
    assert T.submit_to_tmux("sess", "hello", defer_if_busy=True) is True
    sendkeys = [c for c in calls if "send-keys" in c]
    assert sendkeys, "expected send-keys to fire on clean pane"
    assert any("-l" in c for c in sendkeys), "expected literal typing"


def test_submit_defer_if_busy_default_false_ignores_typing(monkeypatch):
    """Manual CLI paths use the default ``defer_if_busy=False``; these
    must NOT skip even when the pane shows pre-existing typing. The
    submit proceeds and clobbers the typed content (which is what
    the CLI user explicitly asked for).

    State machine:
      - Before Enter:  pane shows "> mid-typing" (human was typing,
                       our Escape×2 is about to clobber it).
      - After Enter:   pane shows bare "> " (our submit landed).
    """
    pane_state = {"typed": True}

    def fake_run(argv, **kw):
        if "has-session" in argv:
            return _fake_cp(returncode=0)
        if "send-keys" in argv and "C-m" in argv:
            # Our submit Enter cleared the input.
            pane_state["typed"] = False
        if "capture-pane" in argv:
            content = ["❯ mid-typing"] if pane_state["typed"] else ["❯ "]
            return _fake_cp(stdout=_pane(content))
        return _fake_cp(returncode=0)

    calls: list[list[str]] = []

    def recording(argv, **kw):
        calls.append(list(argv))
        return fake_run(argv, **kw)

    monkeypatch.setattr("subprocess.run", recording)
    monkeypatch.setattr(T, "_find_tmux", lambda: "/usr/bin/tmux")
    # Default — no defer. Must proceed despite visible typing at start.
    assert T.submit_to_tmux("sess", "hello") is True
    sendkeys = [c for c in calls if "send-keys" in c]
    assert sendkeys, "manual submit must not be gated on input-buffer"
    assert any("-l" in c for c in sendkeys), "expected literal typing"


# --- Enter-race post-submit verification (2026-04-16 P0) -------------------
#
# Before this fix: submit_to_tmux returned True as long as no
# ``[Pasted text #`` placeholder was visible, ignoring whether the typed
# text actually got submitted. The Claude TUI sometimes ate the first
# Enter (post-Escape modal, autocomplete popup, paste-buffer commit
# race), leaving our text typed-but-unsubmitted while the function
# lied "True". Symptom: telegram inbound + wake prompts appeared in
# the input box but never became user-turns. Fix: retry Enter while
# EITHER paste-placeholder OR typed-text-in-input-box is still visible.


def test_submit_retries_enter_if_typed_text_remains_in_input(monkeypatch):
    """Simulate Claude TUI eating the first Enter: text stays in the
    input box after the first Enter. The retry loop must fire Enter
    again until the input clears.
    """
    # send-keys-Enter attempts: first one is "eaten" (no effect),
    # the second one actually clears.
    enter_count = {"n": 0}

    def fake_run(argv, **kw):
        if "has-session" in argv:
            return _fake_cp(returncode=0)
        if "send-keys" in argv and "C-m" in argv and "-l" not in argv:
            enter_count["n"] += 1
        if "capture-pane" in argv:
            content = ["❯ PROBE"] if enter_count["n"] < 2 else ["❯ "]
            return _fake_cp(stdout=_pane(content))
        return _fake_cp(returncode=0)

    calls: list[list[str]] = []

    def recording(argv, **kw):
        calls.append(list(argv))
        return fake_run(argv, **kw)

    monkeypatch.setattr("subprocess.run", recording)
    monkeypatch.setattr(T, "_find_tmux", lambda: "/usr/bin/tmux")

    assert T.submit_to_tmux("sess", "PROBE") is True

    # Must have fired Enter at least twice (initial + one retry).
    enter_calls = [c for c in calls
                   if "send-keys" in c and "C-m" in c and "-l" not in c]
    assert len(enter_calls) >= 2, (
        f"expected ≥2 Enter calls (initial + retry), got {len(enter_calls)}"
    )


def test_submit_returns_false_if_enter_never_lands(monkeypatch):
    """Simulate the worst case: Claude TUI eats every Enter (input box
    never clears). After 3 retries, the final check still sees typed
    content → submit_to_tmux MUST return False (not silently lie
    "True"). The fallback Escape was removed 2bf6845 — dirty state is
    left for submit_watchdog."""
    def fake_run(argv, **kw):
        if "has-session" in argv:
            return _fake_cp(returncode=0)
        if "capture-pane" in argv:
            return _fake_cp(stdout=_pane(["❯ STUCK"]))
        return _fake_cp(returncode=0)

    monkeypatch.setattr("subprocess.run", fake_run)
    monkeypatch.setattr(T, "_find_tmux", lambda: "/usr/bin/tmux")
    assert T.submit_to_tmux("sess", "STUCK") is False


