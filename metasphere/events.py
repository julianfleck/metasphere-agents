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
import gzip
import os
import secrets
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
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

    Walks the dated ``events-YYYY-MM-DD.jsonl`` files newest-first until
    *n* lines have been collected, so a tail across a midnight boundary
    transparently spans yesterday and today. Retention-compressed
    ``events-YYYY-MM-DD.jsonl.gz`` files (see :func:`prune_events`) are
    read transparently, so compressing old history never hides it from a
    tail that reaches back far enough. Falls back to the legacy single
    ``events.jsonl`` file only when no dated files exist (transition guard
    for fixtures and freshly-installed hosts).

    Returns ``"(no events)"`` when no log files are present or readable.
    """
    paths = paths or resolve()
    events_dir = paths.events
    dated = []
    if events_dir.is_dir():
        # ``.jsonl`` and ``.jsonl.gz`` for the same date never coexist
        # (compression replaces the plain file), and the fixed-width date
        # prefix means a plain lexical sort still orders by date.
        dated = sorted(
            [*events_dir.glob("events-*.jsonl"), *events_dir.glob("events-*.jsonl.gz")]
        )
    tail: list[str] = []
    if dated:
        # Walk newest-first, collecting up to n lines.
        for log in reversed(dated):
            try:
                opener = gzip.open if log.suffix == ".gz" else open
                with opener(log, "rt", encoding="utf-8") as f:
                    chunk = list(_collections.deque(f, maxlen=n))
            except OSError:
                continue
            # Prepend older-file chunk so final order is chronological.
            tail = chunk + tail
            if len(tail) >= n:
                tail = tail[-n:]
                break
    else:
        legacy = events_dir / "events.jsonl"
        if not legacy.is_file():
            return "(no events)"
        try:
            with open(legacy, "r", encoding="utf-8") as f:
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


# Matches a dated event log, plain or retention-compressed:
#   events-2026-04-16.jsonl       -> group(1)="2026-04-16", group(2)=None
#   events-2026-04-16.jsonl.gz    -> group(1)="2026-04-16", group(2)=".gz"
_DATED_EVENT_RE = re.compile(r"^events-(\d{4}-\d{2}-\d{2})\.jsonl(\.gz)?$")


@dataclass
class PruneResult:
    """Outcome of a :func:`prune_events` call.

    ``bytes_reclaimed`` is the disk freed: the full file size for deletes,
    and ``original - compressed`` for compressions. In ``dry_run`` mode no
    disk is touched and ``bytes_reclaimed`` reflects only deletes (the
    compressed size is unknown without doing the work, so projected
    compressions contribute 0).
    """

    deleted: list[str] = field(default_factory=list)
    compressed: list[str] = field(default_factory=list)
    bytes_reclaimed: int = 0
    dry_run: bool = False


def _dated_event_files(events_dir: Path) -> list[tuple[_dt.date, Path, bool]]:
    """Return ``(date, path, is_compressed)`` for each dated event log."""
    out: list[tuple[_dt.date, Path, bool]] = []
    if not events_dir.is_dir():
        return out
    for child in events_dir.iterdir():
        m = _DATED_EVENT_RE.match(child.name)
        if not m:
            continue
        try:
            d = _dt.date.fromisoformat(m.group(1))
        except ValueError:
            continue
        out.append((d, child, bool(m.group(2))))
    return out


def prune_events(
    older_than_days: int,
    *,
    compress: bool = False,
    dry_run: bool = False,
    paths: Paths | None = None,
) -> PruneResult:
    """Apply retention to dated ``events-YYYY-MM-DD.jsonl`` files.

    Mirrors :func:`metasphere.trace.prune_traces`: files dated strictly
    older than ``today - older_than_days`` are acted on. By default they
    are deleted; with ``compress=True`` each plain file is gzip'd in place
    to ``events-YYYY-MM-DD.jsonl.gz`` instead — a reversible, lossless
    reclaim (~90%) that :func:`tail_events` still reads transparently.

    Note: ``bytes_reclaimed`` for the compress path is the *logical*
    delta (``before - after`` of the file sizes). On a compressing
    filesystem (ZFS/btrfs) the originals are already stored compressed,
    so the physical disk actually freed is materially smaller than this
    figure; ``delete`` frees the full stored size and is the stronger
    reclaim lever on such hosts.

    This is a pure tool: nothing in the harness calls it on a schedule, so
    no events are ever removed automatically. Whether and how often to run
    it is an operator policy decision, exactly like ``metasphere trace
    prune``.

    With ``dry_run=True`` the return value describes what *would* happen
    without touching disk.

    The current day's log is always retained: with ``older_than_days=0``
    the cutoff is today, and only strictly-older files qualify, so the
    live append target is never disturbed.
    """
    paths = paths or resolve()
    events_dir = paths.events
    result = PruneResult(dry_run=dry_run)
    if not events_dir.is_dir():
        return result
    cutoff = _dt.date.today() - _dt.timedelta(days=older_than_days)

    for d, path, is_gz in sorted(_dated_event_files(events_dir)):
        if d >= cutoff:
            continue
        if compress:
            if is_gz:
                continue  # already compressed — nothing to reclaim
            try:
                before = path.stat().st_size
            except OSError:
                continue
            result.compressed.append(path.name)
            if dry_run:
                continue
            gz = path.with_name(path.name + ".gz")
            tmp = path.with_name(path.name + ".gz.tmp")
            try:
                with open(path, "rb") as src, gzip.open(tmp, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                # Make the .gz durable before removing the original, so a
                # crash never destroys the only copy.
                os.replace(tmp, gz)
                after = gz.stat().st_size
                path.unlink()
                result.bytes_reclaimed += max(0, before - after)
            except OSError:
                # Best-effort: clean up a partial temp, leave original intact.
                try:
                    tmp.unlink()
                except OSError:
                    pass
                result.compressed.pop()
        else:
            try:
                size = path.stat().st_size
            except OSError:
                continue
            result.deleted.append(path.name)
            result.bytes_reclaimed += size
            if not dry_run:
                try:
                    path.unlink()
                except OSError:
                    result.deleted.pop()
                    result.bytes_reclaimed -= size

    return result
