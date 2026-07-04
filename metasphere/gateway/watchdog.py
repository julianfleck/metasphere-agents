"""Stuck-prompt recovery for the orchestrator session.

Four failure modes handled:

1. **Stuck pasted-text placeholder.** Bracketed-paste race occasionally
   leaves ``[Pasted text #N +M lines]`` in the pane with the Enter
   eaten. After 15s of the placeholder lingering we force an Enter.
2. **Safety-hooks confirmation prompt.** Plugins occasionally prompt
   ``Do you want to proceed?`` with a numbered ``1. Yes`` option. We
   auto-send ``1`` + Enter, rate-limited to once every 10s so we never
   spam.
3. **Context limit reached.** When Claude's context window fills up,
   the TUI shows ``Context limit reached · /compact or /clear to
   continue`` and stops processing messages silently. We inject
   ``/compact`` to compress the conversation into a summary and resume,
   rate-limited to once every 10 minutes to avoid loops.
4. **Stuck interactive select widget** (``AskUserQuestion`` /
   ``ExitPlanMode``). A gateway session has no keyboard, so an
   interactive select TUI can never receive input and hangs forever.
   The PreToolUse hook (``metasphere.cli.pretool``) normally denies
   those tools *before* they render, so this is a **defense-in-depth
   backstop** for the cases that slip past it: the hook silently
   un-wired (e.g. an ``update.py`` hooks-block drift deletes it) or a
   *new* interactive tool the hook's matcher doesn't yet know. After a
   widget sits **untouched** (byte-stable) for the linger threshold we
   send a single ``Escape`` to cancel it (never ``Escape Escape`` —
   that opens the Rewind menu) and nudge the agent to its async idiom.

All checks are pure functions of capture-pane output + filesystem
state. ``run_watchdog`` composes them.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import time
from typing import Optional

from ..events import log_event
from ..paths import Paths, resolve
from ..session import list_sessions
from .session import SESSION_NAME, session_alive
from ..tmux import submit_to_tmux

_PASTE_RE = re.compile(r"\[Pasted text #\d+")
# Require BOTH a confirm-class line AND a "1. Yes" option line so prose
# listing alone (e.g. an enumeration the agent typed into chat) doesn't
# trip the watchdog.
_SAFETY_HOOKS_PROMPT_RE = re.compile(
    r"(Do you want to proceed\?|\[plugin:safety-hooks\])",
)
_SAFETY_HOOKS_OPTION_RE = re.compile(r"^\s*1\.\s+Yes\b", re.MULTILINE)
# Anchor the context-limit match to the FULL banner on a single line —
# both the ``Context limit reached`` head and the ``/compact or /clear``
# tail — so the bare phrase appearing in scrollback (e.g. an earlier
# banner that has since been compacted away, or the phrase echoed in
# normal output) does not re-fire the check. Mirrors the two-correlated-
# signals discipline of the safety-hooks match above.
_CONTEXT_LIMIT_RE = re.compile(
    r"Context limit reached\b.*?/compact\s+or\s+/clear",
)
# Only inspect the live banner region: the prompt/banner sits at the
# bottom of the pane, so we restrict the match to the last few lines.
_CONTEXT_LIMIT_TAIL_LINES = 8

_STUCK_PASTE_THRESHOLD_S = 15
_SAFETY_HOOKS_RATE_LIMIT_S = 10
_CONTEXT_LIMIT_RATE_LIMIT_S = 600  # 10 minutes

# Interactive select-widget signatures. These are literal footer/option
# strings lifted from the installed Claude Code bundle (v2.1.x), NOT guesses —
# matching the verified-string + two-correlated-signals discipline of the
# checks above rather than puppeteering a guessed pane shape.
#
# Multi-select (``AskUserQuestion``): the toggle-footer is unique enough to
# match alone — it does not appear in normal agent output.
_INTERACTIVE_MULTISELECT_RE = re.compile(r"Space to toggle,\s*Enter to confirm")
# Plan approval (``ExitPlanMode``): require BOTH the question head AND the
# distinctive ``No, keep planning`` option line, so prose merely echoing either
# phrase can't trip the check.
_INTERACTIVE_PLAN_HEAD_RE = re.compile(r"Would you like to proceed\?|Ready to code\?")
_INTERACTIVE_PLAN_OPTION_RE = re.compile(r"No, keep planning")
# The widget sits at the bottom of the pane; only inspect the live region.
_INTERACTIVE_TAIL_LINES = 12
# Escape is the most disruptive recovery (it cancels a tool call), so wait
# longer than the other checks before acting. A genuinely hung widget waits
# forever, so a generous threshold costs nothing while sharply cutting the
# chance of cancelling a human who has attached to answer by hand.
_STUCK_INTERACTIVE_THRESHOLD_S = 45

_INTERACTIVE_REDIRECT_NOTE = (
    "[gateway] An interactive prompt (AskUserQuestion/plan-approval) was "
    "auto-cancelled — this session has no keyboard to answer one and it would "
    "hang you. Ask via your async channel instead: metasphere telegram send "
    "(if you are @orchestrator) or metasphere msg send to your parent/lead "
    "(any other agent), then await the reply in your message inbox, which you "
    "read at the start of every turn. Do not retry the interactive tool."
)


def _tmux_bin() -> str:
    return shutil.which("tmux") or "tmux"


def _capture_pane(session: str) -> str:
    r = subprocess.run(
        [_tmux_bin(), "capture-pane", "-t", session, "-p", "-S", "-50"],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
    )
    return r.stdout if r.returncode == 0 else ""


def _send_keys(session: str, *keys: str) -> None:
    subprocess.run(
        [_tmux_bin(), "send-keys", "-t", session, *keys],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def _session_state_file(paths: Paths, name: str, session_name: str):
    """Return a per-session linger/rate-limit marker path.

    ``run_watchdog`` fans every check out over *all* live ``metasphere-*``
    tmux sessions, so a marker keyed only by ``name`` is shared across
    sessions: one session's tick clobbers (or unlinks) another's timer,
    silently disabling recovery whenever ≥2 sessions are live. Keying the
    marker by ``session_name`` isolates each session's state. tmux session
    names (``metasphere-<...>``) are filename-safe. Mirrors the per-agent
    ``restart_pending.@<agent>.json`` convention already used below.
    """
    return paths.state / f"{name}.{session_name}"


def _read_int(path) -> int:
    try:
        return int(path.read_text().strip())
    except (OSError, ValueError):
        return 0


def _write_int(path, value: int) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(str(value))
    except OSError:
        pass


def check_stuck_paste(
    session_name: str = SESSION_NAME,
    paths: Optional[Paths] = None,
    *,
    now: Optional[int] = None,
) -> bool:
    """Detect a lingering ``[Pasted text #N`` placeholder; force Enter
    if it has been there ≥15s. Returns True if Enter was sent.

    Handles the stuck-paste branch of prompt recovery.
    """
    paths = paths or resolve()
    if not session_alive(session_name):
        return False
    pane = _capture_pane(session_name)
    state_file = _session_state_file(paths, "stuck_paste_seen", session_name)
    if not _PASTE_RE.search(pane):
        # No placeholder — clear the timer.
        try:
            if state_file.exists():
                state_file.unlink()
        except OSError:
            pass
        return False
    now = now if now is not None else int(time.time())
    first = _read_int(state_file)
    if first == 0:
        _write_int(state_file, now)
        return False
    if now - first < _STUCK_PASTE_THRESHOLD_S:
        return False
    # Stuck long enough — force Enter.
    try:
        log_event(
            "supervisor.force_enter",
            "Stuck pasted-text placeholder cleared",
            agent="@daemon-supervisor",
            paths=paths,
        )
    except Exception:
        pass
    _send_keys(session_name, "Enter")
    try:
        state_file.unlink()
    except OSError:
        pass
    return True


def check_safety_hooks_confirmation(
    session_name: str = SESSION_NAME,
    paths: Optional[Paths] = None,
    *,
    now: Optional[int] = None,
) -> bool:
    """Detect a stuck safety-hooks confirmation prompt and auto-approve.

    Rate-limited to once every 10s via a state file marker so we never
    spam ``1`` Enter into the pane. Returns True if a key was sent.
    """
    paths = paths or resolve()
    if not session_alive(session_name):
        return False
    pane = _capture_pane(session_name)
    if not (_SAFETY_HOOKS_PROMPT_RE.search(pane) and _SAFETY_HOOKS_OPTION_RE.search(pane)):
        return False
    marker = _session_state_file(paths, "last_safety_hook_intervention", session_name)
    now = now if now is not None else int(time.time())
    last = _read_int(marker)
    if now - last < _SAFETY_HOOKS_RATE_LIMIT_S:
        return False
    try:
        log_event(
            "supervisor.auto_approve",
            "Safety-hooks confirmation auto-approved",
            agent="@daemon-supervisor",
            paths=paths,
        )
    except Exception:
        pass
    _send_keys(session_name, "1")
    time.sleep(0.2)
    _send_keys(session_name, "Enter")
    _write_int(marker, now)
    return True


def check_context_limit(
    session_name: str = SESSION_NAME,
    paths: Optional[Paths] = None,
    *,
    now: Optional[int] = None,
) -> bool:
    """Detect the ``Context limit reached`` banner and auto-compact.

    When Claude's context window is full the TUI displays::

        Context limit reached · /compact or /clear to continue

    and stops processing new messages silently. This check injects
    ``/compact`` (which summarises the conversation and resumes) rather
    than doing a hard restart, so no conversational context is lost.

    Rate-limited to once every 10 minutes via a state-file marker to
    prevent rapid-fire loops if compaction itself fails. Returns True
    if ``/compact`` was injected.
    """
    paths = paths or resolve()
    if not session_alive(session_name):
        return False
    pane = _capture_pane(session_name)
    # Match the full banner (head + ``/compact or /clear`` tail) and only
    # in the live banner region at the bottom of the pane, so stale
    # scrollback can't re-fire after a successful compact.
    tail = "\n".join(pane.splitlines()[-_CONTEXT_LIMIT_TAIL_LINES:])
    if not _CONTEXT_LIMIT_RE.search(tail):
        return False
    marker = _session_state_file(paths, "last_context_limit_compact", session_name)
    now = now if now is not None else int(time.time())
    last = _read_int(marker)
    if now - last < _CONTEXT_LIMIT_RATE_LIMIT_S:
        return False
    # Route through the guarded submit path (defer_if_busy=True) so we
    # never interleave the ``/compact`` command with a human mid-typing
    # in the pane, and escape_prefix=False so we don't interrupt a
    # running tool. submit_to_tmux types the command and submits with a
    # raw ``C-m`` byte — the tmux ``Enter`` keysym does NOT reliably
    # submit in Claude Code's Ink/React TUI (see metasphere.tmux), so a
    # bare ``Enter`` would type ``/compact`` but never send it while the
    # rate-limit marker still advanced, freezing the session for the full
    # rate-limit window per attempt.
    submitted = submit_to_tmux(
        session_name,
        "/compact",
        defer_if_busy=True,
        escape_prefix=False,
    )
    if not submitted:
        # Deferred (human typing / busy pane) or submit failed — do NOT
        # write the marker, so the next tick retries once the pane frees.
        return False
    try:
        log_event(
            "supervisor.context_limit_compact",
            f"[watchdog] context limit detected in {session_name}, injecting /compact",
            agent="@daemon-supervisor",
            paths=paths,
        )
    except Exception:
        pass
    _write_int(marker, now)
    return True


def _interactive_widget_tail(pane: str) -> Optional[str]:
    """Return the live pane-tail region IFF it shows an interactive select
    widget that would hang a gateway session, else ``None``.

    Matches the multi-select toggle-footer alone (unique), or the plan-approval
    head + ``No, keep planning`` option together (two correlated signals).
    """
    tail = "\n".join(pane.splitlines()[-_INTERACTIVE_TAIL_LINES:])
    if _INTERACTIVE_MULTISELECT_RE.search(tail):
        return tail
    if _INTERACTIVE_PLAN_HEAD_RE.search(tail) and _INTERACTIVE_PLAN_OPTION_RE.search(tail):
        return tail
    return None


def _read_seen_state(path) -> tuple[int, str]:
    """Read a ``<first_ts>\\t<signature>`` linger-state file → ``(ts, sig)``."""
    try:
        ts_str, _, sig = path.read_text().strip().partition("\t")
        return int(ts_str), sig
    except (OSError, ValueError):
        return 0, ""


def _write_seen_state(path, ts: int, sig: str) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{ts}\t{sig}")
    except OSError:
        pass


def check_stuck_interactive_prompt(
    session_name: str = SESSION_NAME,
    paths: Optional[Paths] = None,
    *,
    now: Optional[int] = None,
) -> bool:
    """Backstop: cancel an interactive select widget hanging a gateway session.

    A gateway pane has no keyboard, so ``AskUserQuestion`` / ``ExitPlanMode``
    (and any future interactive select tool) can never receive input and hang
    forever. The PreToolUse hook normally denies those *before* they render;
    this catches the residue — a silently un-wired hook, or a new tool the
    hook's matcher misses.

    Only fires when the widget has sat **untouched** for the linger threshold:
    we fingerprint the widget tail and reset the timer whenever it changes, so
    a human who has attached and is actively navigating the menu (which moves
    the selection highlight, changing the capture) is never cancelled out from
    under. On fire we send a single ``Escape`` (never ``Escape Escape`` — that
    opens the Rewind menu) to cancel, then inject an async-channel nudge.
    Returns True iff a cancel was issued.
    """
    paths = paths or resolve()
    if not session_alive(session_name):
        return False
    pane = _capture_pane(session_name)
    state_file = _session_state_file(paths, "stuck_interactive_seen", session_name)

    tail = _interactive_widget_tail(pane)
    if tail is None:
        # No widget — clear the linger timer.
        try:
            if state_file.exists():
                state_file.unlink()
        except OSError:
            pass
        return False

    sig = hashlib.sha1(tail.encode("utf-8", "replace")).hexdigest()[:16]
    now = now if now is not None else int(time.time())
    first, prev_sig = _read_seen_state(state_file)

    if first == 0 or sig != prev_sig:
        # First sighting, OR the widget changed (someone is interacting /
        # a different prompt) — (re)start the linger timer from now.
        _write_seen_state(state_file, now, sig)
        return False
    if now - first < _STUCK_INTERACTIVE_THRESHOLD_S:
        return False

    # Untouched past the threshold — cancel with a SINGLE Escape and nudge.
    try:
        log_event(
            "supervisor.interactive_prompt_cancel",
            f"[watchdog] stuck interactive widget in {session_name}, "
            "sending Escape + async-channel nudge",
            agent="@daemon-supervisor",
            paths=paths,
        )
    except Exception:
        pass
    _send_keys(session_name, "Escape")
    time.sleep(0.3)  # let the TUI settle back to the prompt before injecting
    # escape_prefix=False: we already cancelled; do NOT fire a second Escape
    # (Escape Escape opens Rewind). defer_if_busy=True: never fight a human
    # mid-type. The note is best-effort — the cancel is the load-bearing fix —
    # so we clear the timer regardless of whether the nudge landed.
    submit_to_tmux(
        session_name,
        _INTERACTIVE_REDIRECT_NOTE,
        defer_if_busy=True,
        escape_prefix=False,
    )
    try:
        state_file.unlink()
    except OSError:
        pass
    return True


# Grace period after restart before injecting the continuation prompt.
# Claude Code needs a few seconds to start up, load CLAUDE.md, and
# display the initial prompt.
_RESTART_GRACE_S = 8
# If the marker is older than this, something went wrong — clear it
# rather than injecting into a session that's been running for ages.
_RESTART_STALE_S = 120


def _check_restart_marker(
    marker: "Path",
    paths: Paths,
    *,
    now: Optional[int] = None,
) -> bool:
    """Process a single restart-pending marker file. Injects a wake-up
    message into the agent's session if the grace period has elapsed.

    Returns True if a wake-up was injected.
    """
    try:
        data = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        try:
            marker.unlink()
        except OSError:
            pass
        return False

    ts = data.get("timestamp", 0)
    reason = data.get("reason", "unknown")
    agent = data.get("agent", "@orchestrator")
    now = now if now is not None else int(time.time())
    age = now - ts

    if age > _RESTART_STALE_S:
        try:
            marker.unlink()
        except OSError:
            pass
        return False

    if age < _RESTART_GRACE_S:
        return False

    # Resolve the session name for this agent. ``_resolve_session``
    # already special-cases @orchestrator → SESSION_NAME and walks the
    # agent registry for project-scoped agents (so agents living in
    # ``metasphere-<project>-<agent>`` sessions are found, not silently
    # missed by the bare ``session_name_for`` form).
    from ..session import _resolve_session

    target_session = _resolve_session(agent)

    if not session_alive(target_session):
        return False

    # Grace period elapsed, session is alive — inject the wake-up.
    try:
        marker.unlink()
    except OSError:
        pass

    from ..telegram.inject import submit_to_tmux as _submit

    wake_msg = (
        f"[session restarted] agent: {agent}, reason: {reason}. "
        "Check messages and tasks, resume where you left off."
    )
    # defer_if_busy=True: post-restart wake is auto-fired. If a human
    # is at the freshly-respawned pane and typing, drop this wake-msg
    # — the next heartbeat will inject context anyway, so the agent
    # still resumes; only the wake-msg phrasing is lost.
    # escape_prefix=False: post-restart wake is an auto-injector and
    # must not interrupt whatever the respawned pane may already be
    # doing — queue the wake text; claude-code will process it when
    # idle.
    success = _submit(
        "system",
        wake_msg,
        session=target_session,
        defer_if_busy=True,
        escape_prefix=False,
    )

    try:
        log_event(
            "supervisor.restart_wake",
            f"Injected continuation prompt for {agent} ({reason})",
            agent="@daemon-supervisor",
            paths=paths,
        )
    except Exception:
        pass

    return success


def check_all_restart_pending(paths: Optional[Paths] = None) -> int:
    """Scan for all per-agent restart markers and process them.

    Returns the number of wake-up messages injected.
    """
    paths = paths or resolve()
    if not paths.state.is_dir():
        return 0
    count = 0
    for marker in paths.state.glob("restart_pending.@*.json"):
        try:
            if _check_restart_marker(marker, paths):
                count += 1
        except Exception as e:
            try:
                log_event(
                    "supervisor.watchdog_error",
                    f"check_restart_marker({marker.name}): {e}",
                    agent="@daemon-supervisor",
                    paths=paths,
                )
            except Exception:
                pass
    return count


def _all_session_names() -> list[str]:
    """Return names of all live metasphere-* tmux sessions."""
    return [s.name for s in list_sessions()]


def run_watchdog(paths: Optional[Paths] = None) -> None:
    """Run all stuck-prompt checks across ALL active agent sessions.

    Enumerates all ``metasphere-*`` tmux sessions and runs per-session
    checks (stuck paste, safety hooks, context limit, stuck interactive
    prompt). Then scans for per-agent restart markers independently.

    Failures of one check do not abort the others. This is the only
    watchdog entry point the daemon calls.
    """
    paths = paths or resolve()

    # Per-session checks: run against every live metasphere-* session.
    sessions = _all_session_names()
    for session_name in sessions:
        for fn in (
            check_stuck_paste,
            check_safety_hooks_confirmation,
            check_context_limit,
            check_stuck_interactive_prompt,
        ):
            try:
                fn(session_name, paths)
            except Exception as e:  # pragma: no cover - defensive
                try:
                    log_event(
                        "supervisor.watchdog_error",
                        f"{fn.__name__}({session_name}): {e}",
                        agent="@daemon-supervisor",
                        paths=paths,
                    )
                except Exception:
                    pass

    # Restart-pending: scan markers (independent of session enumeration).
    try:
        check_all_restart_pending(paths)
    except Exception as e:  # pragma: no cover - defensive
        try:
            log_event(
                "supervisor.watchdog_error",
                f"check_all_restart_pending: {e}",
                agent="@daemon-supervisor",
                paths=paths,
            )
        except Exception:
            pass
