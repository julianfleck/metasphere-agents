"""Per-turn context-hook success breadcrumbs.

Both the UserPromptSubmit context hook and the Stop posthook receive
``session_id`` and ``transcript_path`` in their stdin JSON. The user
prompt is appended to the transcript BEFORE UserPromptSubmit fires; no
new user message is added between UserPromptSubmit and Stop. So a
``(session_id, user_msg_count)`` pair uniquely identifies a turn from
both sides.

The breadcrumb file lets the posthook *fail closed*: if the context
hook crashed (or was never invoked) for the turn we're stopping on,
the posthook MUST NOT route the assistant text to Telegram —
otherwise the user receives a reply that was generated without their
context (messages, tasks, voice capsule, alerts) and the agent looks
amnesic.

Layout::

    ~/.metasphere/state/context-breadcrumbs/<session_id>.json

Schema::

    {
      "session_id": "<uuid>",
      "user_msg_count": <int>,         # transcript user-message count
                                       # at write time
      "status": "success" | "failed",
      "agent": "@orchestrator",
      "timestamp": "2026-04-21T14:30:00Z",
      "reason": "<optional failure detail>"
    }

Pruning: the context hook deletes breadcrumb files older than
``BREADCRUMB_MAX_AGE_SECONDS`` whenever it writes a fresh one. This
keeps the directory bounded without a separate cron job — every
session's breadcrumb gets refreshed by every turn anyway.

This module is pure stdlib + ``metasphere.io``. It must never raise on
the happy path; all helpers swallow OSError and return defensive
defaults so a breadcrumb glitch can never break the host.
"""

from __future__ import annotations

import datetime as _dt
import json
import time
from pathlib import Path

from .io import atomic_write_text
from .paths import Paths


# ---------- constants ----------

STATUS_SUCCESS = "success"
STATUS_FAILED = "failed"

# Stale breadcrumbs are pruned after this many seconds. Long enough to
# survive multi-day sessions where a single REPL idles between turns;
# short enough that orphaned breadcrumbs from killed sessions don't
# accumulate forever. 7 days is the bound the gateway already uses for
# session dormancy.
BREADCRUMB_MAX_AGE_SECONDS = 7 * 24 * 3600


# ---------- paths ----------

def breadcrumbs_dir(paths: Paths) -> Path:
    return paths.state / "context-breadcrumbs"


def breadcrumb_path(paths: Paths, session_id: str) -> Path:
    """Path for ``session_id``'s breadcrumb file.

    ``session_id`` is sanitized to a filesystem-safe slug so a
    pathological payload can't escape the breadcrumbs directory.
    """
    safe = "".join(c if c.isalnum() or c in "-_" else "_" for c in str(session_id))
    safe = safe[:128] or "_unknown_"
    return breadcrumbs_dir(paths) / f"{safe}.json"


# ---------- transcript counting ----------

def count_user_messages(transcript_path: Path | str | None) -> int:
    """Count ``type=="user"`` records in a JSONL transcript.

    Returns 0 when the transcript is missing, empty, unreadable, or has
    no user messages — the posthook treats 0 as "no transcript info"
    and falls through to the fail-closed branch when the breadcrumb
    can't be matched.
    """
    if not transcript_path:
        return 0
    p = Path(transcript_path)
    if not p.exists():
        return 0
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return 0
    n = 0
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("type") == "user":
            n += 1
    return n


# ---------- write / read ----------

def _utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_breadcrumb(
    paths: Paths,
    *,
    session_id: str,
    status: str,
    user_msg_count: int,
    agent: str = "",
    reason: str = "",
) -> bool:
    """Write a breadcrumb atomically. Returns True on success, False on
    OSError. Never raises.
    """
    if not session_id:
        return False
    record = {
        "session_id": session_id,
        "user_msg_count": int(user_msg_count),
        "status": status,
        "agent": agent or "",
        "timestamp": _utcnow(),
    }
    if reason:
        record["reason"] = reason
    path = breadcrumb_path(paths, session_id)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(path, json.dumps(record, sort_keys=True) + "\n")
        return True
    except OSError:
        return False


def read_breadcrumb(paths: Paths, session_id: str) -> dict | None:
    """Read the breadcrumb for ``session_id``. Returns None if missing
    or unreadable.
    """
    if not session_id:
        return None
    path = breadcrumb_path(paths, session_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


# ---------- pruning ----------

def prune_old_breadcrumbs(paths: Paths, *, max_age_seconds: int = BREADCRUMB_MAX_AGE_SECONDS) -> int:
    """Delete breadcrumb files older than ``max_age_seconds``. Returns
    the count removed. Never raises.
    """
    bdir = breadcrumbs_dir(paths)
    if not bdir.is_dir():
        return 0
    now = time.time()
    cutoff = now - max_age_seconds
    removed = 0
    try:
        entries = list(bdir.glob("*.json"))
    except OSError:
        return 0
    for entry in entries:
        try:
            if entry.stat().st_mtime < cutoff:
                entry.unlink(missing_ok=True)
                removed += 1
        except OSError:
            continue
    return removed


# ---------- decision helper ----------

def evaluate(
    paths: Paths,
    *,
    session_id: str,
    transcript_path: Path | str | None,
) -> tuple[bool, str]:
    """Decide whether the posthook should forward this turn.

    Returns ``(ok, reason)``:
      - ``ok=True`` means the breadcrumb is present, marks success, and
        matches this turn's transcript user-message count.
      - ``ok=False`` with a one-word ``reason`` describing why
        (``"no-session-id"``, ``"breadcrumb-missing"``,
        ``"context-hook-failed"``, ``"count-mismatch"``,
        ``"session-mismatch"``).
    """
    if not session_id:
        return False, "no-session-id"
    bc = read_breadcrumb(paths, session_id)
    if bc is None:
        return False, "breadcrumb-missing"
    if bc.get("status") != STATUS_SUCCESS:
        return False, "context-hook-failed"
    if str(bc.get("session_id") or "") != str(session_id):
        return False, "session-mismatch"
    expected = int(bc.get("user_msg_count") or 0)
    actual = count_user_messages(transcript_path)
    if expected != actual:
        return False, "count-mismatch"
    return True, "ok"
