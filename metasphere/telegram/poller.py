"""Long-polling getUpdates loop with atomic offset persistence.

Replaces the bash ``while curl ... getUpdates`` loop in
scripts/metasphere-telegram-stream. Improvements:

- Offset is written via tmp+rename under fcntl.flock so a concurrent
  reader/writer cannot observe a half-written file or race a lost update.
- Updates are returned as ``Update`` dataclasses instead of being parsed
  ad-hoc with jq at every call site.
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from typing import Iterable, Iterator, List, Optional

from . import api

DEFAULT_OFFSET_PATH = os.path.expanduser("~/.metasphere/telegram/offset")


@dataclass
class Update:
    update_id: int
    message_id: Optional[int]
    chat_id: Optional[int]
    chat_is_forum: bool
    thread_id: Optional[int]
    from_username: Optional[str]
    text: Optional[str]
    date: Optional[int]
    raw: dict = field(repr=False)

    @classmethod
    def from_payload(cls, payload: dict) -> "Update":
        msg = payload.get("message") or payload.get("edited_message") or {}
        chat = msg.get("chat") or {}
        frm = msg.get("from") or {}
        return cls(
            update_id=payload["update_id"],
            message_id=msg.get("message_id"),
            chat_id=chat.get("id"),
            chat_is_forum=bool(chat.get("is_forum")),
            thread_id=msg.get("message_thread_id"),
            from_username=frm.get("username") or frm.get("first_name"),
            text=msg.get("text"),
            date=msg.get("date"),
            raw=payload,
        )


def _ensure_parent(path: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)


def load_offset(path: str = DEFAULT_OFFSET_PATH) -> int:
    if not os.path.exists(path):
        return 0
    with open(path, "r") as f:
        try:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            data = f.read().strip()
        finally:
            fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    return int(data) if data else 0


def save_offset(offset: int, path: str = DEFAULT_OFFSET_PATH) -> None:
    """Atomically persist ``offset`` to ``path`` under an exclusive lock.

    The bash version did ``echo "$x" > offset``, which truncates first
    and then writes. A concurrent reader (or a crash mid-write) could see
    an empty file and reset to offset 0, replaying the entire backlog.
    Tmp+rename avoids that, and the lock prevents two writers from
    interleaving.
    """
    _ensure_parent(path)
    lock_path = path + ".lock"
    with open(lock_path, "w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            fd, tmp = tempfile.mkstemp(prefix=".offset.", dir=os.path.dirname(path))
            try:
                with os.fdopen(fd, "w") as f:
                    f.write(str(offset))
                os.replace(tmp, path)
            except Exception:
                if os.path.exists(tmp):
                    os.unlink(tmp)
                raise
        finally:
            fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


def get_updates(offset: int = 0, timeout: int = 30) -> List[Update]:
    """Single getUpdates call. Blocks up to ``timeout`` seconds."""
    resp = api.call(
        "getUpdates",
        offset=offset,
        timeout=timeout,
        allowed_updates=json.dumps(["message"]),
    )
    return [Update.from_payload(p) for p in resp.get("result", [])]


def poll(
    timeout: int = 30,
    offset_path: str = DEFAULT_OFFSET_PATH,
    stop: Optional[callable] = None,
) -> Iterator[Update]:
    """Yield ``Update``s forever, persisting offset after each update.

    Pass a ``stop`` callable returning True to break the loop (used in
    tests). In production this is a daemon: it never returns.
    """
    offset = load_offset(offset_path)
    while True:
        if stop is not None and stop():
            return
        try:
            updates = get_updates(offset=offset, timeout=timeout)
        except api.TelegramAPIError as e:
            # Log and back off briefly so we don't hot-loop on a 5xx.
            print(f"[poller] api error: {e}", flush=True)
            time.sleep(2)
            continue
        for u in updates:
            yield u
            offset = u.update_id + 1
            save_offset(offset, offset_path)
