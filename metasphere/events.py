"""Append-only structured event log.

Replaces ad-hoc ``echo {...} >> ~/.metasphere/events/events.jsonl``
patterns. All writes go through ``io.append_jsonl`` which holds an
exclusive flock for the duration of the append, so concurrent
producers (multiple agents, hooks, schedulers) cannot tear records.

Schema: one JSON object per line with the fields
``{id, timestamp, type, message, agent, scope, meta}``.
"""

from __future__ import annotations

import datetime as _dt
import os
import secrets
import time
from typing import Any

import collections as _collections
import json
import re

from .identity import resolve_agent_id
from .io import append_jsonl
from .paths import Paths, resolve


def _event_id() -> str:
    return f"evt-{int(time.time() * 1000)}-{os.getpid()}-{secrets.token_hex(2)}"


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _scope_rel(paths: Paths) -> str:
    try:
        rel = paths.scope.resolve().relative_to(paths.project_root.resolve())
        s = "/" + str(rel)
    except ValueError:
        return str(paths.scope)
    return s.rstrip("/") or "/"


def log_event(
    type: str,
    message: str,
    *,
    agent: str | None = None,
    scope: str | None = None,
    meta: dict[str, Any] | None = None,
    paths: Paths | None = None,
) -> dict[str, Any]:
    """Append one event record. Returns the record for inspection/testing."""
    paths = paths or resolve()
    record: dict[str, Any] = {
        "id": _event_id(),
        "timestamp": _now_iso(),
        "type": type,
        "message": message,
        "agent": agent if agent is not None else resolve_agent_id(paths),
        "scope": scope if scope is not None else _scope_rel(paths),
        "meta": meta or {},
    }
    append_jsonl(paths.events_log, record)
    return record


def tail_events(n: int = 10, *, paths: Paths | None = None) -> str:
    """Return the last *n* events formatted as human-readable lines.

    Output matches the bash ``metasphere-events tail`` format::

        HH:MM:SSZ [type] @agent: message (truncated to 80 chars)

    Returns ``"(no events)"`` when the log file is missing or empty.
    """
    paths = paths or resolve()
    log = paths.events_log
    if not log.is_file():
        return "(no events)"
    try:
        with open(log, "r", encoding="utf-8") as f:
            tail = list(_collections.deque(f, maxlen=n))
    except OSError:
        return "(no events)"
    if not tail:
        return "(no events)"
    lines: list[str] = []
    for raw in tail:
        raw = raw.strip()
        if not raw:
            continue
        try:
            rec = json.loads(raw)
        except json.JSONDecodeError:
            continue
        ts = rec.get("timestamp", "")
        # Extract HH:MM:SSZ from ISO timestamp like 2024-01-01T12:34:56Z
        time_part = ts.split("T", 1)[1].split(".")[0] if "T" in ts else ts
        typ = rec.get("type", "")
        agent = rec.get("agent", "")
        msg = rec.get("message", "") or ""
        # Strip newlines and truncate to 80 chars, matching bash version
        msg = re.sub(r"[\n\r]", " ", msg)[:80]
        lines.append(f"{time_part} [{typ}] {agent}: {msg}")
    return "\n".join(lines) if lines else "(no events)"
