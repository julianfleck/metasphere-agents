"""Credential failsafe: auto-rotate Anthropic OAuth profiles on rate-limit.

Probes the orchestrator's tmux pane for rate-limit signals before each
heartbeat injection.  If a signal is found and the cooldown period has
elapsed, rotates to the next available credential profile via the
``accounts`` module's helpers.

Public API
----------
``probe_and_rotate(session, paths, agent="@orchestrator") -> bool``
    Call this just before injecting a heartbeat.  Returns True if a
    rotation was performed.  ``agent`` is the agent whose pane is
    being probed; used only for log/notify attribution since the
    rotation itself is global (rate-limit hits the shared account).
"""

from __future__ import annotations

import re
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable, Optional

# ---------------------------------------------------------------------------
# Rate-limit detection patterns
# ---------------------------------------------------------------------------

# Strings that appear in the claude-code TUI when the account is rate-limited.
# Conservative set — prefer false-negatives over false-positives.
_RATE_LIMIT_PATTERNS: tuple[str, ...] = (
    # claude-code / anthropic API errors
    "rate_limit_error",
    "RateLimitError",
    "429 Too Many Requests",
    "Too Many Requests",
    # Claude.ai plan-level limits
    "usage limit",
    "Usage limit",
    "usage_limit_error",
    "reached your limit",
    "reached its limit",
    "Claude.ai usage limit",
    # Generic anthropic SDK messages
    "rate limited",
    "Rate limited",
)

# How many tail lines from the pane to inspect.
_PANE_TAIL_LINES = 60

# Minimum seconds between two consecutive rotations (per process).
# Prevents flip-flopping if the pane still shows a stale error.
_COOLDOWN_SECONDS = 600  # 10 minutes

# ---------------------------------------------------------------------------
# Cooldown state (in-process; resets on daemon restart)
# ---------------------------------------------------------------------------

_last_rotation_ts: float = float("-inf")  # -inf ensures first call always passes cooldown


def _cooldown_elapsed() -> bool:
    return (time.monotonic() - _last_rotation_ts) >= _COOLDOWN_SECONDS


def _mark_rotated() -> None:
    global _last_rotation_ts
    _last_rotation_ts = time.monotonic()


# ---------------------------------------------------------------------------
# Pane capture
# ---------------------------------------------------------------------------


def _capture_pane(session: str) -> str:
    """Return the visible pane text for *session*, or '' on failure."""
    from ..tmux import PYTEST_TMUX_SENTINEL, tmux_sandboxed
    # The sentinel execs to FileNotFoundError, caught by the OSError
    # branch below — a sandboxed test never reads a live pane.
    tmux = PYTEST_TMUX_SENTINEL if tmux_sandboxed() else "tmux"
    try:
        r = subprocess.run(
            [tmux, "capture-pane", "-p", "-t", session],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        return r.stdout if r.returncode == 0 else ""
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _pane_has_rate_limit(pane_text: str) -> bool:
    """Return True if any rate-limit pattern appears in *pane_text*."""
    tail = "\n".join(pane_text.splitlines()[-_PANE_TAIL_LINES:])
    return any(p in tail for p in _RATE_LIMIT_PATTERNS)


# ---------------------------------------------------------------------------
# Courtesy reply on rate-limit (inbound surface notification)
# ---------------------------------------------------------------------------
#
# When an agent hits a Claude usage/rate limit it can't generate, so an
# inbound Slack/Telegram user gets silence. Credential rotation
# (probe_and_rotate) only helps on a managed multi-profile setup and only
# notifies the orchestrator — never the originating user, and nothing at
# all on the common shared-single-token case. This path is decoupled from
# rotation entirely: it fires at the gateway inbound, where the origin
# surface+channel is known, regardless of whether rotation succeeded or was
# skipped (skip_unmanaged / no spare profile).

#: The claude-code TUI renders the limit reset as e.g. "Resets 3pm",
#: "usage limit reached — your limit will reset at 3:00 PM (UTC)". Capture
#: the time token (optional ``am/pm`` and an optional trailing timezone
#: paren). Best-effort — a miss falls back to generic wording.
_RESET_TIME_RE = re.compile(
    r"reset[s]?(?:\s+(?:at|by))?\s+"
    r"(\d{1,2}(?::\d{2})?\s*(?:[ap]\.?m\.?)?(?:\s*\([^)]{0,30}\))?)",
    re.IGNORECASE,
)

#: Minimum seconds between two courtesy replies for the SAME agent. Mirrors
#: the rotation cooldown so a still-stale pane doesn't fire a courtesy reply
#: on every inbound (or heartbeat) for the duration of the limit window. A
#: change in the parsed reset time bypasses this (a genuinely new window).
_COURTESY_COOLDOWN_SECONDS = 1800  # 30 minutes

#: Per-agent ``(monotonic_ts, reset_signature)`` of the last courtesy reply.
#: In-process (resets on daemon restart), keyed by agent so each inbound
#: surface is deduped independently — the limit is account-wide but the
#: users to notify are not.
_last_courtesy: dict[str, tuple[float, Optional[str]]] = {}


def parse_reset_time(pane_text: str) -> Optional[str]:
    """Parse the limit reset time from *pane_text*, or None if absent.

    Inspects the same tail window as :func:`_pane_has_rate_limit` so a
    stale reset string scrolled off the top can't be picked up.
    """
    tail = "\n".join(pane_text.splitlines()[-_PANE_TAIL_LINES:])
    m = _RESET_TIME_RE.search(tail)
    if not m:
        return None
    return " ".join(m.group(1).split()).strip() or None


def courtesy_message(reset: Optional[str]) -> str:
    """The one-line courtesy reply shown to an inbound user on rate-limit."""
    if reset:
        return (
            f"⏳ I've hit my usage limit (resets {reset}); "
            "I'll reply when it clears."
        )
    return "⏳ I've hit my usage limit; I'll reply once it clears."


def _should_courtesy(agent: str, reset: Optional[str]) -> bool:
    """Dedup gate: True iff a courtesy reply for *agent* is due now.

    Fires on the first hit, then suppresses repeats within the cooldown —
    UNLESS the parsed reset time changed, which signals a genuinely new
    limit window that warrants a fresh notification.
    """
    prev = _last_courtesy.get(agent)
    if prev is None:
        return True
    last_ts, last_reset = prev
    if reset is not None and reset != last_reset:
        return True
    return (time.monotonic() - last_ts) >= _COURTESY_COOLDOWN_SECONDS


def _mark_courtesy(agent: str, reset: Optional[str]) -> None:
    _last_courtesy[agent] = (time.monotonic(), reset)


def maybe_courtesy_reply(
    agent: str,
    session: str,
    send: Callable[[str], None],
) -> bool:
    """Probe *agent*'s pane; if rate-limited, send ONE courtesy reply.

    ``send`` is a surface-bound callback (it already knows the origin
    Slack channel / Telegram chat) taking the message text. Deduped per
    agent via :func:`_should_courtesy`. Never raises — a probe/send failure
    must not break the inbound injection path. Returns True if a courtesy
    reply was sent.

    Unlike :func:`probe_and_rotate` this does NOT skip on darwin (a usage
    limit is account-level, independent of where credentials live) and does
    NOT depend on rotation — it is the shared-token case's only signal.
    """
    try:
        pane_text = _capture_pane(session)
        if not pane_text or not _pane_has_rate_limit(pane_text):
            return False
        reset = parse_reset_time(pane_text)
        if not _should_courtesy(agent, reset):
            return False
        send(courtesy_message(reset))
        _mark_courtesy(agent, reset)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Round-robin account selection
# ---------------------------------------------------------------------------


def _next_profile(accounts_dir: Path, live_cred: Path) -> Optional[str]:
    """Return the name of the next profile to rotate to, or None.

    Sorts profiles alphabetically, finds the current one, returns
    ``(current_index + 1) % len(profiles)``.  Returns None if fewer
    than 2 profiles exist (no rotation possible).
    """
    from .accounts import CRED_FILENAME

    if not accounts_dir.is_dir():
        return None
    profiles = sorted(
        child.name
        for child in accounts_dir.iterdir()
        if child.is_dir() and (child / CRED_FILENAME).is_file()
    )
    if len(profiles) < 2:
        return None  # nothing to rotate to

    # Identify current.
    current: Optional[str] = None
    if live_cred.is_symlink():
        try:
            target = live_cred.resolve(strict=False)
            rel = target.relative_to(accounts_dir.resolve(strict=False))
            parts = rel.parts
            if len(parts) == 2 and parts[1] == CRED_FILENAME:
                current = parts[0]
        except (ValueError, OSError):
            pass

    if current is None or current not in profiles:
        return profiles[0]  # unknown current → pick first

    idx = profiles.index(current)
    return profiles[(idx + 1) % len(profiles)]


# ---------------------------------------------------------------------------
# Rotation
# ---------------------------------------------------------------------------


def _do_rotate(next_name: str, accounts_dir: Path, live_cred: Path) -> bool:
    """Perform the atomic symlink swap.  Returns True on success."""
    from .accounts import CRED_FILENAME, _atomic_symlink

    target = accounts_dir / next_name / CRED_FILENAME
    if not target.is_file():
        return False
    try:
        _atomic_symlink(target, live_cred)
    except Exception:
        return False
    return True


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def probe_and_rotate(
    session: str,
    paths,  # metasphere.paths.Paths — typed loosely to avoid circular import
    agent: str = "@orchestrator",
) -> bool:
    """Probe *session* for a rate-limit signal; rotate credentials if found.

    Returns True if a rotation was performed, False otherwise.  Never
    raises — failures are logged and swallowed so the caller (heartbeat)
    stays alive.

    ``agent`` attributes the detection to whichever agent's pane caught
    the signal.  The rotation itself is global — a rate-limit anywhere
    in the harness implies the shared Anthropic account is throttled —
    but the log and telegram notification name the detecting agent so
    it's clear where the signal originated.

    Skipped when:
    - ``sys.platform == "darwin"`` (credentials live in keychain there).
    - Cooldown has not elapsed since the last rotation.
    - Fewer than 2 profiles are configured.
    - No rate-limit pattern found in the pane.
    """
    if sys.platform == "darwin":
        return False
    if not _cooldown_elapsed():
        return False

    try:
        pane_text = _capture_pane(session)
        if not pane_text or not _pane_has_rate_limit(pane_text):
            return False

        from .accounts import ACCOUNTS_DIR, LIVE_CRED

        # Refuse to rotate when the live credentials file is a regular
        # file rather than a metasphere-managed symlink.  Replacing it
        # would clobber the user's original credentials with no backup,
        # since a real file at LIVE_CRED was never imported into a
        # profile.  The user must run ``metasphere accounts add`` and
        # ``metasphere accounts switch`` to bring the file under
        # management before the failsafe can act.
        if LIVE_CRED.exists() and not LIVE_CRED.is_symlink():
            try:
                from ..events import log_event
                log_event(
                    "failsafe.skip_unmanaged",
                    f"rate-limit detected on {agent} pane but {LIVE_CRED} "
                    f"is not a metasphere-managed symlink; skipping "
                    f"rotation to avoid clobbering original credentials",
                    agent=agent,
                    paths=paths,
                )
            except Exception:
                pass
            return False

        next_name = _next_profile(ACCOUNTS_DIR, LIVE_CRED)
        if next_name is None:
            return False

        if not _do_rotate(next_name, ACCOUNTS_DIR, LIVE_CRED):
            return False

        _mark_rotated()

        # Log + notify — both are best-effort.
        try:
            from ..events import log_event
            log_event(
                "failsafe.rotate",
                f"rate-limit detected on {agent} pane; rotated to {next_name}",
                agent=agent,
                paths=paths,
            )
        except Exception:
            pass

        try:
            from ..posthook import _resolve_chat_id
            from ..telegram import api as telegram_api
            chat_id = _resolve_chat_id(paths)
            if chat_id:
                telegram_api.send_message(
                    chat_id,
                    f"Rate limit detected on {agent} pane — rotated to "
                    f"credentials profile '{next_name}'.",
                )
        except Exception:
            pass

        return True

    except Exception:
        return False
