"""Stuck-prompt recovery for the orchestrator session.

Two failure modes handled:

1. **Stuck pasted-text placeholder.** Bracketed-paste race occasionally
   leaves ``[Pasted text #N +M lines]`` in the pane with the Enter
   eaten. After 15s of the placeholder lingering we force an Enter.
2. **Safety-hooks confirmation prompt.** Plugins occasionally prompt
   ``Do you want to proceed?`` with a numbered ``1. Yes`` option. We
   auto-send ``1`` + Enter, rate-limited to once every 10s so we never
   spam.

Both checks are pure functions of capture-pane output + filesystem
state. ``run_watchdog`` composes them.
"""

from __future__ import annotations

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

_PASTE_RE = re.compile(r"\[Pasted text #\d+")
# Require BOTH a confirm-class line AND a "1. Yes" option line so prose
# listing alone (e.g. an enumeration the agent typed into chat) doesn't
# trip the watchdog.
_SAFETY_HOOKS_PROMPT_RE = re.compile(
    r"(Do you want to proceed\?|\[plugin:safety-hooks\])",
)
_SAFETY_HOOKS_OPTION_RE = re.compile(r"^\s*1\.\s+Yes\b", re.MULTILINE)

_STUCK_PASTE_THRESHOLD_S = 15
_SAFETY_HOOKS_RATE_LIMIT_S = 10


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
    state_file = paths.state / "stuck_paste_seen"
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
    marker = paths.state / "last_safety_hook_intervention"
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

    # Resolve the session name for this agent.
    from ..agents import session_name_for
    from .session import _restart_marker_path

    # For orchestrator, use the canonical SESSION_NAME (historical naming).
    if agent == "@orchestrator":
        target_session = SESSION_NAME
    else:
        target_session = session_name_for(agent)

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
    success = _submit("system", wake_msg, session=target_session, defer_if_busy=True)

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
    checks (stuck paste, safety hooks). Then scans for per-agent restart
    markers independently.

    Failures of one check do not abort the others. This is the only
    watchdog entry point the daemon calls.
    """
    paths = paths or resolve()

    # Per-session checks: run against every live metasphere-* session.
    sessions = _all_session_names()
    for session_name in sessions:
        for fn in (check_stuck_paste, check_safety_hooks_confirmation):
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
