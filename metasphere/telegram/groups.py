"""Telegram forum-topic management (port of scripts/metasphere-telegram-groups).

Wraps the Bot API ``createForumTopic`` / ``sendMessage`` calls and
persists the local name → topic-id mapping under
``$METASPHERE_DIR/telegram/groups/topics.json``. The bash
``process_forum_command`` branch was unreachable per PORTING dead-code
audit and is intentionally not ported.
"""

from __future__ import annotations

import datetime as _dt
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

from ..io import read_json, write_json
from ..paths import Paths, resolve
from . import api as tg_api


@dataclass
class Topic:
    id: int
    name: str
    created: str

    def to_dict(self) -> dict:
        return asdict(self)


def _topics_file(paths: Paths) -> Path:
    return paths.telegram / "groups" / "topics.json"


def _forum_id_file(paths: Paths) -> Path:
    return paths.config / "telegram_forum_id"


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_forum_id(paths: Optional[Paths] = None) -> Optional[str]:
    paths = paths or resolve()
    f = _forum_id_file(paths)
    if not f.exists():
        return None
    return f.read_text(encoding="utf-8").strip() or None


def _load_topics(paths: Paths) -> dict:
    return read_json(_topics_file(paths), default={}) or {}


def _save_topics(paths: Paths, data: dict) -> None:
    write_json(_topics_file(paths), data)


def create_topic(name: str, *, icon_emoji: str = "📋",
                 paths: Optional[Paths] = None) -> Topic:
    paths = paths or resolve()
    forum_id = get_forum_id(paths)
    if not forum_id:
        raise RuntimeError("forum not configured (~/.metasphere/config/telegram_forum_id missing)")
    resp = tg_api.call("createForumTopic", chat_id=forum_id, name=name)
    if not resp.get("ok"):
        raise RuntimeError(f"createForumTopic failed: {resp.get('description', 'unknown error')}")
    result = resp["result"]
    topic = Topic(
        id=int(result["message_thread_id"]),
        name=result.get("name", name),
        created=_now_iso(),
    )
    data = _load_topics(paths)
    data[name] = topic.to_dict()
    _save_topics(paths, data)
    return topic


def list_topics(*, paths: Optional[Paths] = None) -> list[Topic]:
    paths = paths or resolve()
    return [Topic(**v) for v in _load_topics(paths).values()]


def resolve_topic_id(topic: str | int, *, paths: Optional[Paths] = None) -> Optional[int]:
    if isinstance(topic, int) or (isinstance(topic, str) and topic.isdigit()):
        return int(topic)
    paths = paths or resolve()
    data = _load_topics(paths)
    entry = data.get(topic)
    if not entry:
        return None
    return int(entry["id"])


def send_to_topic(topic: str | int, text: str, *,
                  agent: str = "@orchestrator",
                  paths: Optional[Paths] = None) -> dict:
    paths = paths or resolve()
    forum_id = get_forum_id(paths)
    if not forum_id:
        raise RuntimeError("forum not configured")
    topic_id = resolve_topic_id(topic, paths=paths)
    if topic_id is None:
        raise LookupError(f"topic not found: {topic}")
    # L5 (wave-4 review): escape underscores so agent names like
    # @reviewer_quality don't get rendered italic by Markdown.
    safe_agent = agent.replace("_", r"\_")
    body = f"*[{safe_agent}]*\n\n{text}"
    resp = tg_api.call(
        "sendMessage",
        chat_id=forum_id,
        message_thread_id=topic_id,
        parse_mode="Markdown",
        text=body,
    )
    if not resp.get("ok"):
        raise RuntimeError(f"sendMessage failed: {resp.get('description')}")
    return resp


def topic_link(topic: str | int, *, paths: Optional[Paths] = None) -> str:
    paths = paths or resolve()
    forum_id = get_forum_id(paths)
    if not forum_id:
        raise RuntimeError("forum not configured")
    topic_id = resolve_topic_id(topic, paths=paths)
    if topic_id is None:
        raise LookupError(f"topic not found: {topic}")
    chat_clean = forum_id[4:] if forum_id.startswith("-100") else forum_id.lstrip("-")
    return f"https://t.me/c/{chat_clean}/{topic_id}"


def workspace(kind: str, name: str, *, id: str = "",
              paths: Optional[Paths] = None) -> str:
    """Create a topic for a project/task/agent and return its deep link."""
    emoji = {"project": "📁", "task": "✅", "agent": "🤖"}.get(kind, "📋")
    topic_name = f"{name} [{id}]" if id else name
    t = create_topic(topic_name, icon_emoji=emoji, paths=paths)
    return topic_link(t.id, paths=paths)
