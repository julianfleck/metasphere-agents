"""Stuck-prompt recovery for the orchestrator session.

Two failure modes the bash gateway recovered from, ported here:

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

import re
import shutil
import subprocess
import time
from typing import Optional

from ..events import log_event
from ..paths import Paths, resolve
from .session import SESSION_NAME, session_alive

_PASTE_RE = re.compile(r"\[Pasted text #\d+")
_SAFETY_HOOKS_RE = re.compile(
    r"(Do you want to proceed\?|\[plugin:safety-hooks\]|^\s*1\.\s+Yes\b)",
    re.MULTILINE,
)

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

    Mirrors ``check_stuck_prompts``'s paste branch.
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
    if not _SAFETY_HOOKS_RE.search(pane):
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


def run_watchdog(paths: Optional[Paths] = None) -> None:
    """Run all stuck-prompt checks. Failures of one check do not abort
    the others. This is the only watchdog entry point the daemon calls.
    """
    paths = paths or resolve()
    for fn in (check_stuck_paste, check_safety_hooks_confirmation):
        try:
            fn(SESSION_NAME, paths)
        except Exception as e:  # pragma: no cover - defensive
            try:
                log_event(
                    "supervisor.watchdog_error",
                    f"{fn.__name__}: {e}",
                    agent="@daemon-supervisor",
                    paths=paths,
                )
            except Exception:
                pass
