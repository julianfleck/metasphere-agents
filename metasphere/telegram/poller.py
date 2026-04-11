"""Long-polling getUpdates loop with atomic offset persistence.

- Offset is written via tmp+rename under fcntl.flock so a concurrent
  reader/writer cannot observe a half-written file or race a lost update.
- Updates are returned as ``Update`` dataclasses for typed access.
"""

from __future__ import annotations

import fcntl
import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Iterator, List, Optional

from ..io import atomic_write_text, file_lock
from . import api

DEFAULT_OFFSET_PATH = os.path.expanduser("~/.metasphere/telegram/offset")


#: Max characters of the quoted original kept for context rendering.
REPLY_PREVIEW_MAX = 100


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
    # Reply metadata — set when this message is a reply to another one.
    reply_to_message_id: Optional[int] = None
    reply_to_text_preview: Optional[str] = None
    # Kind discriminator. "message" is the default; "reaction" is emitted
    # for message_reaction updates (which have no `message` field, just
    # old_reaction / new_reaction arrays and a message_id reference).
    kind: str = "message"
    # Reaction payload — only set when ``kind == "reaction"``.
    reaction_emojis: List[str] = field(default_factory=list)
    reaction_target_message_id: Optional[int] = None
    raw: dict = field(repr=False, default_factory=dict)

    @classmethod
    def from_payload(cls, payload: dict) -> "Update":
        # message_reaction updates carry no `message`; they arrive as
        # `{"update_id": N, "message_reaction": {...}}`. Route them to a
        # dedicated builder so downstream code can dispatch on ``kind``.
        if "message_reaction" in payload:
            return cls._from_reaction(payload)

        msg = payload.get("message") or payload.get("edited_message") or {}
        chat = msg.get("chat") or {}
        frm = msg.get("from") or {}

        reply_to_id: Optional[int] = None
        reply_preview: Optional[str] = None
        reply = msg.get("reply_to_message")
        if reply:
            reply_to_id = reply.get("message_id")
            reply_text = reply.get("text") or reply.get("caption") or ""
            if reply_text:
                reply_preview = reply_text[:REPLY_PREVIEW_MAX]

        return cls(
            update_id=payload["update_id"],
            message_id=msg.get("message_id"),
            chat_id=chat.get("id"),
            chat_is_forum=bool(chat.get("is_forum")),
            thread_id=msg.get("message_thread_id"),
            from_username=frm.get("username") or frm.get("first_name"),
            text=msg.get("text"),
            date=msg.get("date"),
            reply_to_message_id=reply_to_id,
            reply_to_text_preview=reply_preview,
            kind="message",
            raw=payload,
        )

    @classmethod
    def _from_reaction(cls, payload: dict) -> "Update":
        """Build a reaction-kind Update from a ``message_reaction`` payload.

        Telegram shape (abridged)::

            {
              "update_id": 42,
              "message_reaction": {
                "chat": {"id": -100..., "is_forum": true},
                "message_id": 17,
                "user": {"id": 99, "username": "j0lian"},
                "date": 1700000000,
                "old_reaction": [],
                "new_reaction": [{"type": "emoji", "emoji": "👍"}]
              }
            }

        We surface the *new* reaction list as the authoritative set (i.e.
        what Julian now shows on the message), because the heartbeat
        renderer only cares about the current state, not the diff.
        """
        mr = payload.get("message_reaction") or {}
        chat = mr.get("chat") or {}
        user = mr.get("user") or {}
        new_reaction = mr.get("new_reaction") or []
        emojis = [
            r.get("emoji")
            for r in new_reaction
            if isinstance(r, dict) and r.get("type") == "emoji" and r.get("emoji")
        ]
        return cls(
            update_id=payload["update_id"],
            message_id=None,
            chat_id=chat.get("id"),
            chat_is_forum=bool(chat.get("is_forum")),
            thread_id=None,
            from_username=user.get("username") or user.get("first_name"),
            text=None,
            date=mr.get("date"),
            kind="reaction",
            reaction_emojis=emojis,
            reaction_target_message_id=mr.get("message_id"),
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
    """Atomically persist ``offset`` under a sidecar flock.

    Routes through ``io.file_lock`` (which never truncates the lock fd
    and never unlinks it) + ``atomic_write_text`` (tmp+rename with fsync).
    """
    _ensure_parent(path)
    with file_lock(Path(path + ".lock")):
        atomic_write_text(Path(path), str(offset))


#: Update types we ask Telegram to deliver. ``message_reaction`` requires
#: the bot to be an admin in the chat (group/channel) *and* that the bot
#: explicitly lists it in ``allowed_updates`` — it is NOT sent by default.
ALLOWED_UPDATES = ("message", "edited_message", "message_reaction")


def get_updates(offset: int = 0, timeout: int = 30) -> List[Update]:
    """Single getUpdates call. Blocks up to ``timeout`` seconds."""
    resp = api.call(
        "getUpdates",
        offset=offset,
        timeout=timeout,
        allowed_updates=json.dumps(list(ALLOWED_UPDATES)),
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
            try:
                yield u
                offset = u.update_id + 1
                save_offset(offset, offset_path)
            except (KeyError, TypeError, ValueError) as e:
                # Malformed payload — log and skip rather than crashing
                # the poll loop. Without this guard a bad update_id field
                # would tear down the daemon.
                print(f"[poller] skipping malformed update: {e}", flush=True)
                continue
