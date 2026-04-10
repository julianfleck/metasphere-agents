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

from .identity import resolve_agent_id
from .io import append_jsonl
from .paths import Paths, resolve


def _event_id() -> str:
    return f"evt-{int(time.time() * 1000)}-{os.getpid()}-{secrets.token_hex(2)}"


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _scope_rel(paths: Paths) -> str:
    try:
        rel = paths.scope.resolve().relative_to(paths.repo.resolve())
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
