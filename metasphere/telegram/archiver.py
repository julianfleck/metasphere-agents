"""Append-only archive of incoming/outgoing Telegram messages.

Mirrors the bash ``archive_message`` + ``save_latest`` pair from
metasphere-telegram-stream:

- ``~/.metasphere/telegram/stream/YYYY-MM-DD.jsonl`` — daily JSONL log.
- ``~/.metasphere/telegram/latest.json`` — most recent message, used for
  context injection.

All writes go through fcntl.LOCK_EX so multiple poll workers (or the
poller racing with an outgoing-message archive) cannot interleave bytes
inside a JSONL line.
"""

from __future__ import annotations

import datetime as _dt
import fcntl
import json
import os
import tempfile
from typing import Optional

DEFAULT_DIR = os.path.expanduser("~/.metasphere/telegram")
STREAM_SUBDIR = "stream"
LATEST_NAME = "latest.json"


def _today_path(base: str) -> str:
    day = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    return os.path.join(base, STREAM_SUBDIR, f"{day}.jsonl")


def _ensure_dirs(base: str) -> None:
    os.makedirs(os.path.join(base, STREAM_SUBDIR), exist_ok=True)


def archive_message(message: dict, base_dir: str = DEFAULT_DIR) -> str:
    """Append ``message`` (raw Telegram message dict) to today's JSONL.

    Returns the path written to. Acquires LOCK_EX on the file for the
    duration of the write so concurrent appends don't interleave.
    """
    _ensure_dirs(base_dir)
    path = _today_path(base_dir)
    line = json.dumps(message, ensure_ascii=False, separators=(",", ":")) + "\n"
    # Open in append mode; flock the fd; write; release.
    with open(path, "a", encoding="utf-8") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            f.write(line)
            f.flush()
            os.fsync(f.fileno())
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return path


def save_latest(message: dict, base_dir: str = DEFAULT_DIR) -> str:
    """Atomically rewrite ``latest.json`` with a context-friendly summary."""
    _ensure_dirs(base_dir)
    path = os.path.join(base_dir, LATEST_NAME)
    frm = message.get("from") or {}
    summary = {
        "message_id": message.get("message_id"),
        "from": frm.get("username") or frm.get("first_name"),
        "text": message.get("text"),
        "date": message.get("date"),
        "timestamp": _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "chat_id": (message.get("chat") or {}).get("id"),
    }
    fd, tmp = tempfile.mkstemp(prefix=".latest.", dir=base_dir)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False)
        os.replace(tmp, path)
    except Exception:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise
    return path


def archive_outgoing(
    agent: str, text: str, chat_id: int, base_dir: str = DEFAULT_DIR
) -> str:
    """Record an outgoing message in the same JSONL stream."""
    payload = {
        "from": {"username": agent.lstrip("@")},
        "text": text,
        "chat": {"id": chat_id},
        "date": int(_dt.datetime.now(_dt.timezone.utc).timestamp()),
        "outgoing": True,
    }
    return archive_message(payload, base_dir=base_dir)
