"""Reliable tmux paste+submit for claude TUI sessions.

Bypasses bracketed-paste entirely by using ``tmux send-keys -l`` (literal
mode) to type the message character-by-character. Newlines within the
message are sent as ``C-j`` (newline-in-buffer, does not submit); final
submit is a single ``Enter``.

Belt-and-suspenders: after submitting, captures the pane and checks for
a stuck ``[Pasted text #`` placeholder. If found, retries Enter up to
3 times.

Never raises — returns False on failure.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import time


def _find_tmux() -> str | None:
    """Locate the tmux binary."""
    return shutil.which("tmux")


def _has_session(tmux: str, session: str) -> bool:
    """Return True if a tmux session exists."""
    try:
        r = subprocess.run(
            [tmux, "has-session", "-t", session],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return r.returncode == 0
    except OSError:
        return False


def _input_line_has_typing(tmux: str, session: str) -> bool:
    """Inspect the pane and return True if the input box shows
    user-typed content (mid-typing human).

    2026-04-16: Julian was typing into the attached orchestrator pane
    when a heartbeat fired ``submit_to_tmux``; its send-keys interleaved
    with his keystrokes and submitted the garbled mess. This guard
    inspects the Claude TUI input box BEFORE firing any send-keys; if
    the prompt line shows typed content that isn't a known paste
    placeholder, auto-injectors defer.

    A fcntl writer-lock cannot help here — human keystrokes go via the
    tty, bypassing in-process locks. And a broader check ("is any
    client attached?") overreaches — Julian keeps the pane attached
    for monitoring, so attach-alone is not evidence of typing. Looking
    at the input-buffer state is the precise primitive.

    Heuristic: capture-pane, walk back through the last visible lines
    looking for a Claude TUI prompt marker after stripping any
    box-drawing border chars. Claude Code renders the prompt as ``❯``
    (U+276F); older stubs/tests also used ASCII ``>``, so both are
    accepted. If the content after the marker is empty whitespace OR a
    known ``[Pasted text #`` placeholder, the input is "ours to use";
    anything else means a human is mid-typing and we defer rather than
    blow their input away with our own typing or with the Escape×2
    pre-clear.

    2026-04-16: the original check matched ``>`` only and therefore
    never fired against a real Claude Code pane — leaving the PR #27
    Enter-retry second signal dead on arrival, and causing
    ``submit_to_tmux`` to return True after the first Enter whether or
    not it actually landed (the race that ate the mid-tool-call
    telegram inbound).

    Fails open on any error (returns False) — better to occasionally
    interleave than to silently drop every heartbeat on tmux quirks.
    """
    try:
        r = subprocess.run(
            [tmux, "capture-pane", "-p", "-t", session],
            capture_output=True,
            text=True,
            check=False,
        )
        if r.returncode != 0:
            return False
        lines = r.stdout.splitlines()
        for line in reversed(lines[-10:]):
            inner = line.strip().lstrip("│|").rstrip("│|").strip()
            if inner.startswith("❯"):
                marker_len = len("❯")
            elif inner.startswith(">"):
                marker_len = 1
            else:
                continue
            after = inner[marker_len:].strip()
            if not after:
                return False
            if "[Pasted text #" in after:
                return False
            return True
        return False
    except OSError:
        return False


def _has_pending_paste(tmux: str, session: str) -> bool:
    """Return True if a ``[Pasted text #`` placeholder is visible in the
    last few lines of the session pane."""
    try:
        r = subprocess.run(
            [tmux, "capture-pane", "-p", "-t", session],
            capture_output=True,
            text=True,
            check=False,
        )
        if r.returncode != 0:
            return False
        # Check last 5 lines, matching the bash script's `tail -5`
        lines = r.stdout.splitlines()
        for line in lines[-5:]:
            if "[Pasted text #" in line:
                return True
        return False
    except OSError:
        return False


def submit_to_tmux(
    session: str, message: str, *, defer_if_busy: bool = False
) -> bool:
    """Deliver *message* to a claude TUI in tmux session *session*.

    Strategy: split on newlines, send each line via ``tmux send-keys -l``
    (literal mode). Between lines send ``C-j`` (newline in buffer, does
    not submit). After the last line, brief settle then ``Enter`` to
    submit. Verifies no stuck ``[Pasted text #`` placeholder remains;
    retries Enter up to 3 times if one is found.

    Returns True on success, False on any failure. Never raises.

    If *defer_if_busy* is True, abort (returning False, no send-keys
    fired) when the input box shows typed content (see
    :func:`_input_line_has_typing`). Auto-injectors (heartbeat,
    agent-to-agent wakes, telegram inject, posthook deferred-cmd,
    restart-wake) opt in; manual CLI paths leave it off so a
    user-initiated send still goes through. Importantly, this does NOT
    check for client attachment — Julian keeps panes attached for
    monitoring and attach-alone isn't evidence of typing; we guard on
    actual input-buffer content instead.
    """
    try:
        tmux = _find_tmux()
        if not tmux:
            return False

        if not _has_session(tmux, session):
            return False

        if defer_if_busy and _input_line_has_typing(tmux, session):
            print(
                f"[tmux.submit] defer: input has typing in {session}",
                file=sys.stderr,
            )
            return False

        # Pre-emptive Escape × 2: clears any ``[Pasted text #N``
        # placeholder left over from a prior wake that didn't fully
        # commit. Without this, stacked pastes accumulated in the
        # research-* agent panes overnight (2026-04-16 accelerator-
        # programs had 2 stuck pastes; each new cron-fired wake just
        # added another on top, the Enter-retry loop saw "placeholder
        # still present" and gave up). Escape is the manual-recovery
        # primitive Julian uses when he notices; doing it proactively
        # stops the stacking at the source. Double-press in case the
        # REPL is mid-character-input (first Escape cancels partial
        # input, second Escape cancels any pending paste buffer).
        subprocess.run(
            [tmux, "send-keys", "-t", session, "Escape", "Escape"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        time.sleep(0.15)

        # Split message into lines, preserving empty trailing lines
        lines = message.split("\n")

        for i, line in enumerate(lines):
            if line:
                subprocess.run(
                    [tmux, "send-keys", "-t", session, "-l", "--", line],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )
            if i < len(lines) - 1:
                # Newline-in-buffer (does NOT submit in claude TUI)
                subprocess.run(
                    [tmux, "send-keys", "-t", session, "C-j"],
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    check=False,
                )

        # Settle, then submit
        time.sleep(0.3)
        subprocess.run(
            [tmux, "send-keys", "-t", session, "Enter"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        time.sleep(0.4)

        # Retry Enter if the submit didn't actually land. TWO
        # independent "still-not-submitted" signals:
        #   (a) a ``[Pasted text #N`` placeholder is visible (the old
        #       bracketed-paste path — stale from a prior inject or
        #       from our own Escape×2 prefix racing paste-mode).
        #   (b) our typed text is still visible in the input box (the
        #       ``send-keys -l`` literal path — Claude TUI sometimes
        #       eats the first Enter due to a post-Escape modal, an
        #       autocomplete popup, or a paste-buffer commit race).
        # Without (b) the retry loop saw "no placeholder" and returned
        # True while the text sat typed-but-unsubmitted — the 2026-04-16
        # P0 telegram-inbound / wake-Enter race. Function was silently
        # lying about success.
        for _ in range(3):
            if (not _has_pending_paste(tmux, session)
                    and not _input_line_has_typing(tmux, session)):
                return True
            subprocess.run(
                [tmux, "send-keys", "-t", session, "Enter"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            time.sleep(0.4)

        # Enter-retry exhausted and the input is still dirty. One
        # last aggressive attempt: Escape to cancel whatever is pending.
        # Better to drop the current submission than to stack yet
        # another paste/typed-text for future callers to trip on (the
        # accumulation pattern that caused the 2026-04-16 research-
        # monitor outage).
        if (_has_pending_paste(tmux, session)
                or _input_line_has_typing(tmux, session)):
            subprocess.run(
                [tmux, "send-keys", "-t", session, "Escape", "Escape"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            time.sleep(0.2)

        # Final check after retries — both signals must be clean.
        return (not _has_pending_paste(tmux, session)
                and not _input_line_has_typing(tmux, session))

    except Exception:
        return False


def submit_watchdog(session: str) -> bool:
    """Scan *session* for a stale ``[Pasted text #`` placeholder and force
    Enter if found.

    Intended to run periodically from the gateway daemon. Returns True if
    no action was needed or recovery succeeded; False on hard failure.
    Never raises.
    """
    try:
        tmux = _find_tmux()
        if not tmux:
            return False

        if not _has_session(tmux, session):
            return True  # no session = nothing to fix

        if not _has_pending_paste(tmux, session):
            return True  # clean

        # Force submit, up to 2 attempts
        for _ in range(2):
            subprocess.run(
                [tmux, "send-keys", "-t", session, "Enter"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            time.sleep(0.5)
            if not _has_pending_paste(tmux, session):
                return True

        return False

    except Exception:
        return False
