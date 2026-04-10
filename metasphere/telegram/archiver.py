"""Append-only archive of incoming/outgoing Telegram messages.

Archive layout:

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


def telegram_context(history: int = 3, base_dir: str = DEFAULT_DIR) -> str:
    """Return the last *history* telegram messages formatted as context text.

    Reads today's (and optionally yesterday's) stream archive JSONL files
    and formats them the same way the bash ``metasphere-telegram-stream
    context --history N`` command does.

    Returns a section header + formatted messages, or a
    ``(no recent messages)`` fallback.
    """
    stream_dir = os.path.join(base_dir, STREAM_SUBDIR)
    today = _dt.datetime.now(_dt.timezone.utc)
    today_str = today.strftime("%Y-%m-%d")
    yesterday_str = (today - _dt.timedelta(days=1)).strftime("%Y-%m-%d")

    today_file = os.path.join(stream_dir, f"{today_str}.jsonl")
    yesterday_file = os.path.join(stream_dir, f"{yesterday_str}.jsonl")

    def _read_jsonl(path: str) -> list[dict]:
        if not os.path.isfile(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines = f.readlines()
        except OSError:
            return []
        objs: list[dict] = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                objs.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return objs

    # Collect messages: today first, backfill from yesterday if needed
    today_msgs = _read_jsonl(today_file)
    msgs = today_msgs[-history:]
    if len(msgs) < history:
        need = history - len(msgs)
        yesterday_msgs = _read_jsonl(yesterday_file)
        msgs = yesterday_msgs[-need:] + msgs

    if not msgs:
        # Fallback: try latest.json
        latest_path = os.path.join(base_dir, LATEST_NAME)
        if os.path.isfile(latest_path):
            try:
                with open(latest_path, "r", encoding="utf-8") as f:
                    latest = json.load(f)
                frm = latest.get("from") or "unknown"
                text = latest.get("text") or ""
                ts = latest.get("timestamp") or ""
                if text and text != "null":
                    return (
                        "## Telegram (last message)\n"
                        "\n"
                        f"**@{frm}** ({ts}):\n"
                        f"> {text}\n"
                    )
            except (OSError, json.JSONDecodeError):
                pass
        return "## Telegram: No recent messages\n"

    out = ["## Telegram (recent conversation)", ""]
    for o in msgs:
        frm_field = o.get("from")
        if isinstance(frm_field, dict):
            frm = frm_field.get("username") or frm_field.get("first_name") or "unknown"
        elif isinstance(frm_field, str):
            frm = frm_field
        else:
            frm = "unknown"
        text = o.get("text") or ""
        if not text or text == "null":
            continue
        date_ts = o.get("date") or 0
        ts = ""
        if date_ts and date_ts != "null":
            try:
                ts = _dt.datetime.fromtimestamp(
                    float(date_ts), _dt.timezone.utc
                ).strftime("%H:%M")
            except (TypeError, ValueError, OSError):
                pass
        direction = "\u2192" if o.get("outgoing") else "\u2190"
        out.append(f"{direction} **@{frm}** ({ts}): {text}")

    out.append("")
    out.append('_Reply via: `metasphere-telegram send "message"`_')
    return "\n".join(out) + "\n"


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
