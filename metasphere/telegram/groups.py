"""Telegram forum-topic management.

Wraps the Bot API ``createForumTopic`` / ``sendMessage`` calls and
persists the local name → topic-id mapping under
``$METASPHERE_DIR/telegram/groups/topics.json``.
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


@dataclass
class ForumStatus:
    """Result of inspecting a candidate forum group."""

    forum_id: str
    title: str
    chat_type: str
    is_forum: bool
    bot_is_admin: bool
    can_manage_topics: bool
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return (
            self.error is None
            and self.is_forum
            and self.bot_is_admin
            and self.can_manage_topics
        )

    def describe_problem(self) -> Optional[str]:
        if self.error:
            return self.error
        if self.chat_type not in ("supergroup", "group"):
            return f"chat type is {self.chat_type!r}, expected supergroup"
        if not self.is_forum:
            return (
                "topics are not enabled on this group "
                "(Group Settings → Topics → Enable)"
            )
        if not self.bot_is_admin:
            return "bot is not an admin in this group"
        if not self.can_manage_topics:
            return "bot is admin but lacks the 'Manage Topics' permission"
        return None


def verify_forum(forum_id: str, *, paths: Optional[Paths] = None) -> ForumStatus:
    """Inspect a candidate forum group via getChat + getChatMember.

    This is a read-only probe; it never writes config. Use ``setup_forum``
    to persist a verified id.
    """
    paths = paths or resolve()
    try:
        chat_resp = tg_api.call("getChat", chat_id=forum_id)
    except Exception as e:  # noqa: BLE001 — network/api/etc
        return ForumStatus(
            forum_id=str(forum_id),
            title="",
            chat_type="",
            is_forum=False,
            bot_is_admin=False,
            can_manage_topics=False,
            error=f"getChat failed: {e}",
        )
    chat = chat_resp.get("result", {})
    chat_type = chat.get("type", "")
    title = chat.get("title", "")
    is_forum = bool(chat.get("is_forum"))

    bot_is_admin = False
    can_manage_topics = False
    err: Optional[str] = None
    try:
        me = tg_api.call("getMe").get("result", {})
        bot_id = me.get("id")
        if bot_id is not None:
            mem = tg_api.call(
                "getChatMember", chat_id=forum_id, user_id=bot_id
            ).get("result", {})
            status = mem.get("status")
            bot_is_admin = status in ("administrator", "creator")
            # creators implicitly have all permissions; admins must have the flag.
            can_manage_topics = (
                status == "creator"
                or bool(mem.get("can_manage_topics"))
            )
    except Exception as e:  # noqa: BLE001
        err = f"getChatMember failed: {e}"

    return ForumStatus(
        forum_id=str(forum_id),
        title=title,
        chat_type=chat_type,
        is_forum=is_forum,
        bot_is_admin=bot_is_admin,
        can_manage_topics=can_manage_topics,
        error=err,
    )


def setup_forum(forum_id: str, *, force: bool = False,
                paths: Optional[Paths] = None) -> ForumStatus:
    """Validate ``forum_id`` and, if it passes, persist it to the config file.

    Args:
        forum_id: Telegram supergroup id (typically starts with ``-100``).
        force: Save the id even if validation reports problems. Useful when
            the operator knows topics will be enabled imminently.
        paths: paths override (tests).

    Raises:
        RuntimeError: if validation fails and ``force`` is False. The
            message includes the specific problem detected.
    """
    paths = paths or resolve()
    status = verify_forum(forum_id, paths=paths)
    if not status.ok and not force:
        problem = status.describe_problem() or "unknown validation failure"
        raise RuntimeError(
            f"forum {forum_id!r} is not usable: {problem}. "
            f"Telegram bots cannot create supergroups or enable topics — "
            f"a human must create the group, enable Topics, and add the bot "
            f"as an admin with 'Manage Topics' permission."
        )
    paths.config.mkdir(parents=True, exist_ok=True)
    _forum_id_file(paths).write_text(str(forum_id) + "\n", encoding="utf-8")
    return status


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
    # Escape underscores so agent names like @reviewer_quality don't
    # get rendered italic by Markdown.
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
