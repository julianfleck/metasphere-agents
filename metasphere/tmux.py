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


def _is_box_border(stripped: str) -> bool:
    """A Claude Code input-box border line is a run of ``─`` (U+2500)
    characters, optionally with leading/trailing spaces. Some terminals
    render it as ASCII ``-`` — accept both."""
    if not stripped:
        return False
    # Must be predominantly border chars (allow a few stray spaces).
    border_chars = sum(1 for c in stripped if c in "─-")
    return border_chars >= 10 and border_chars >= len(stripped) * 0.8


def _input_line_has_typing(tmux: str, session: str) -> bool:
    """Inspect the pane and return True if the input box shows
    user-typed content (mid-typing human, or mid-inject residue).

    2026-04-16: Julian was typing into the attached orchestrator pane
    when a heartbeat fired ``submit_to_tmux``; its send-keys interleaved
    with his keystrokes and submitted the garbled mess. This guard
    inspects the Claude TUI input box BEFORE firing any send-keys; if
    the prompt shows typed content that isn't a known paste placeholder,
    auto-injectors defer.

    Heuristic: find Claude Code's input box by its ``─────`` border
    lines (U+2500). Input box content lives between the last two
    border lines in the visible pane. If any line between them has
    content beyond the ``❯`` prompt marker and whitespace, someone is
    typing.

    The earlier version walked only the last 10 lines looking for a
    line starting with ``❯`` — which missed wrapped multi-line input,
    because Claude Code pushes the ``❯``-line off the window when the
    user types a long message. Heartbeats then fired mid-typing and
    interleaved with keystrokes. The border-based detection handles
    wrapped input correctly (all wrapped content sits between the
    same two borders).

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
        # Walk up from the end looking for the two border lines that
        # bracket the input box.
        bottom_idx: int | None = None
        top_idx: int | None = None
        for i in range(len(lines) - 1, -1, -1):
            if _is_box_border(lines[i].strip()):
                if bottom_idx is None:
                    bottom_idx = i
                else:
                    top_idx = i
                    break
        if bottom_idx is None or top_idx is None:
            return False  # no input box found — fail open
        # Inspect every line between the borders (exclusive).
        for line in lines[top_idx + 1:bottom_idx]:
            # Strip the box side chars and whitespace.
            inner = line.strip().lstrip("│|").rstrip("│|").strip()
            if not inner:
                continue
            # Strip the ❯ / > prompt marker if this is the first line.
            if inner.startswith("❯"):
                inner = inner[len("❯"):].strip()
            elif inner.startswith(">"):
                inner = inner[1:].strip()
            if not inner:
                continue
            # A lingering paste placeholder is not typing — submit_watchdog
            # handles those asynchronously.
            if "[Pasted text #" in inner:
                continue
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
    session: str, message: str, *,
    defer_if_busy: bool = False,
    escape_prefix: bool = True,
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
    agent-to-agent wakes, posthook deferred-cmd, restart-wake) opt in;
    manual CLI paths and user-inbound telegram leave it off so the send
    still goes through. Importantly, this does NOT check for client
    attachment — Julian keeps panes attached for monitoring and
    attach-alone isn't evidence of typing; we guard on actual
    input-buffer content instead.

    If *escape_prefix* is True (default), fire ``Escape × 2`` before
    typing to clear any stuck paste placeholder AND to interrupt any
    in-flight Claude Code turn — so the pasted message becomes a new
    user-turn rather than queuing behind a running tool. Auto-injectors
    (heartbeat, agent-to-agent wakes, posthook deferred-cmd,
    restart-wake) set this to False: they must never interrupt a
    running tool call, only user-inbound telegram and manual CLI sends
    should. 2026-04-16: the always-on Escape was eating Julian's
    telegram inbound AND his own typing whenever a heartbeat fired
    during a mid-tool-call; separating "interrupt intent" from "paste
    intent" closes that race "once and for all" (his framing).
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

        # Pre-emptive Escape × 2: (a) clears any ``[Pasted text #N``
        # placeholder left over from a prior wake that didn't fully
        # commit (stacking pattern that caused the 2026-04-16
        # research-* pane outages); (b) interrupts any in-flight
        # Claude Code turn so the pasted text becomes a NEW user-turn
        # rather than queueing behind a running tool.
        #
        # Gated on *escape_prefix* so auto-injectors can paste without
        # interrupting a running tool. When False we rely on Claude
        # Code's keystroke queue: characters typed during a tool call
        # are buffered and processed when the tool completes. The
        # downside is that if a prior inject left a stale paste
        # placeholder, the auto-path can't clean it up — but the
        # submit_watchdog daemon handles that asynchronously.
        if escape_prefix:
            # SINGLE Escape = interrupt running turn (Julian's
            # Claude Code keybinding reference, 2026-04-16). Esc Esc
            # opens the Rewind/Undo menu — we were typing into THAT
            # menu's filter the whole time, which explains the
            # "list of messages flashing" symptom. Never Escape×2
            # here.
            subprocess.run(
                [tmux, "send-keys", "-t", session, "Escape"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            # 800ms gives the TUI time to finish its post-interrupt
            # "What should Claude do instead?" transition before we
            # type. Shorter settles eat the first 1-2 chars.
            time.sleep(0.8)

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

        # Settle, then single C-m. The TUI's submit handler can take
        # 3-5 seconds to process a multi-line buffer (rendering catches
        # up first, then commit, then the agent starts processing).
        # The previous code fired retry C-m every 400ms, which spammed
        # extra submits while the TUI was still working on the first
        # one — those extra C-m landed mid-process and either reset
        # the input, produced duplicate user-turns, or left dirt that
        # future wakes stacked on. That stacking is the 2026-04-20
        # frame-semantics + common-law buffered-not-submitted incident.
        #
        # 2026-04-20 repro: typed 30 lines, fired single C-m, captured
        # at t+1.5s ("still dirty") and t+4.5s ("agent has replied").
        # The C-m landed at t=0; the dirty-check at t+0.4s was a false
        # positive caused by render lag. One C-m is enough — we just
        # need to wait for it.
        time.sleep(0.3)
        subprocess.run(
            # C-m (ASCII 0x0D) raw byte. tmux 3.3a's 'Enter' keysym
            # doesn't trigger submit in Claude Code's TUI (Ink/React)
            # in all states; raw C-m does.
            [tmux, "send-keys", "-t", session, "C-m"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )

        # Poll for clean input box, up to 6s. Fast path returns in
        # <1s on the common case; patient on slow TUI processing.
        # No retry C-m firing — single submit, just wait for it.
        for _ in range(12):
            time.sleep(0.5)
            if (not _has_pending_paste(tmux, session)
                    and not _input_line_has_typing(tmux, session)):
                return True

        # 6s elapsed and the input is still dirty. Don't fire a recovery
        # C-m here — that's what created the stacking bug. Leave the
        # buffered content for submit_watchdog to handle on its next
        # daemon tick, OR for the next intentional caller to overwrite.
        return False

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
                # C-m (ASCII 0x0D) instead of the 'Enter' keysym. tmux 3.3a's
                # 'Enter' keysym doesn't trigger submit in Claude Code's
                # TUI (Ink/React), but raw C-m does. Root cause of the
                # 2026-04-20 wake-Enter race: text typed but Enter keysym
                # silently dropped by the TUI input handler.
                [tmux, "send-keys", "-t", session, "C-m"],
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
