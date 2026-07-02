"""Slack API client — outbound message + document path.

Mirrors :mod:`metasphere.telegram.api` so the cross-surface dispatcher
in ``metasphere.cli.message`` can route to either surface without
caring about which one it is.

Tokens are loaded per ``surface_id``: env vars (``SLACK_BOT_TOKEN``,
``SLACK_APP_TOKEN``) win, then ``~/.metasphere/config/<surface_id>.env``,
then ``~/.metasphere/config/slack.env`` as the legacy single-bot
default. Errors raise ``FileNotFoundError`` so the CLI can surface
"drop tokens at this path" rather than a generic Slack auth error.
"""

from __future__ import annotations

import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Slack's chat.postMessage soft limit is 4000 chars; leave margin for
# the multi-chunk ``[i/N]`` marker so a long send still renders cleanly.
CHUNK_MAX = 3500

CONFIG_DIR = os.path.expanduser("~/.metasphere/config")


def _read_env_file(path: str, key: str) -> Optional[str]:
    if not os.path.exists(path):
        return None
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            if k.strip() == key:
                return v.strip().strip('"').strip("'")
    return None


def _load_tokens(surface_id: str) -> tuple[str, str]:
    """Resolve ``(bot_token, app_token)`` for ``surface_id``.

    Resolution order:

    1. Env: ``SLACK_BOT_TOKEN`` / ``SLACK_APP_TOKEN`` (single-bot fast path).
    2. ``~/.metasphere/config/<surface_id>.env``.
    3. ``~/.metasphere/config/slack.env`` (legacy single-bot default).

    Raises ``FileNotFoundError`` listing the locations tried if no
    ``SLACK_BOT_TOKEN`` can be resolved. ``SLACK_APP_TOKEN`` is allowed
    to be empty — only :class:`metasphere.slack.poller.run_poll_iteration`
    (Socket Mode) needs it; outbound ``send_message`` works on the bot
    token alone.
    """
    tried: list[str] = []

    bot_env = os.environ.get("SLACK_BOT_TOKEN")
    app_env = os.environ.get("SLACK_APP_TOKEN")
    if bot_env:
        return bot_env, app_env or ""

    per_surface = os.path.join(CONFIG_DIR, f"{surface_id}.env")
    tried.append(per_surface)
    bot = _read_env_file(per_surface, "SLACK_BOT_TOKEN")
    app = _read_env_file(per_surface, "SLACK_APP_TOKEN")
    if bot:
        return bot, app or ""

    default = os.path.join(CONFIG_DIR, "slack.env")
    tried.append(default)
    bot = _read_env_file(default, "SLACK_BOT_TOKEN")
    app = _read_env_file(default, "SLACK_APP_TOKEN")
    if bot:
        return bot, app or ""

    raise FileNotFoundError(
        "No SLACK_BOT_TOKEN found for surface_id "
        f"{surface_id!r}: tried env vars + {tried}. Drop a token file "
        f"at {per_surface} with `SLACK_BOT_TOKEN=xoxb-...` and "
        f"`SLACK_APP_TOKEN=xapp-...`."
    )


def _split_chunks(text: str, max_len: int = CHUNK_MAX) -> list[str]:
    """Split ``text`` at paragraph/line boundaries within ``max_len``.

    Same shape as :func:`metasphere.telegram.api._split_chunks` so the
    chunk-formatting behavior is consistent across surfaces.
    """
    if len(text) <= max_len:
        return [text]
    chunks: list[str] = []
    remaining = text
    while remaining:
        if len(remaining) <= max_len:
            chunks.append(remaining)
            break
        window = remaining[:max_len]
        cut = window.rfind("\n\n")
        if cut < max_len // 2:
            cut = window.rfind("\n")
        if cut < max_len // 2:
            cut = max_len
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    return chunks


def _web_client(bot_token: str):
    """Lazy WebClient construction — keeps slack-sdk import cheap."""
    from slack_sdk import WebClient  # local import = lazy slack-sdk load
    return WebClient(token=bot_token)


def send_message(surface_id: str, channel: str, text: str) -> list[dict]:
    """Send ``text`` to ``channel`` (Slack channel id like ``"C12345"``).

    Auto-chunks at ``CHUNK_MAX`` so long messages don't bounce off
    chat.postMessage's 4000-char ceiling. Returns the WebClient
    response dicts (one per chunk).
    """
    if not text:
        raise ValueError("send_message: text must be non-empty")
    bot_token, _ = _load_tokens(surface_id)
    client = _web_client(bot_token)
    chunks = _split_chunks(text)
    total = len(chunks)
    responses: list[dict] = []
    for i, chunk in enumerate(chunks, start=1):
        body = chunk if total == 1 else f"[{i}/{total}] {chunk}"
        resp = client.chat_postMessage(channel=channel, text=body)
        # SlackResponse acts like a dict via __getitem__; cast for type
        # tools.
        responses.append(dict(resp.data) if hasattr(resp, "data") else dict(resp))
    return responses


def send_document(
    surface_id: str,
    channel: str,
    file_path: str,
    title: Optional[str] = None,
) -> dict:
    """Upload ``file_path`` to ``channel`` via files_upload_v2."""
    bot_token, _ = _load_tokens(surface_id)
    client = _web_client(bot_token)
    resp = client.files_upload_v2(
        channel=channel, file=file_path, title=title,
    )
    return dict(resp.data) if hasattr(resp, "data") else dict(resp)


# --------------------------------------------------------------------------
# User-name resolution (slack uid → display name).
#
# REQUIRES the ``users:read`` bot-token scope. The first-light token does NOT
# carry it (Slack returns ``missing_scope``), so both helpers below degrade to
# ``None`` / ``[]`` rather than crashing inbound — the caller falls back to the
# raw uid. Grant ``users:read`` + reinstall to enable, then
# ``metasphere addressbook sync-slack`` can bulk-populate names.
# --------------------------------------------------------------------------

def _display_name(member: dict) -> Optional[str]:
    """Pick the friendliest name off a slack user record."""
    profile = member.get("profile") or {}
    for cand in (
        profile.get("display_name"),
        profile.get("real_name"),
        member.get("real_name"),
        member.get("name"),
    ):
        if cand and str(cand).strip():
            return str(cand).strip()
    return None


def resolve_user_name(
    surface_id: str, user_id: str, *, client=None,
) -> Optional[str]:
    """Resolve a slack ``user_id`` → display name via ``users.info``, or None.

    LIVE-BLOCKED until the bot token gains ``users:read`` (Slack returns
    ``missing_scope`` today → this returns ``None`` and the caller keeps the
    raw uid). Guarded so the scope gap never raises into the inbound path.
    ``client`` is a test seam — pass a stub WebClient to unit-test without a
    live call.
    """
    if not user_id:
        return None
    try:
        if client is None:
            bot_token, _ = _load_tokens(surface_id)
            client = _web_client(bot_token)
        resp = client.users_info(user=user_id)
        data = resp.data if hasattr(resp, "data") else resp
        member = (data or {}).get("user") or {}
        return _display_name(member)
    except Exception as e:  # noqa: BLE001 — missing_scope / network must not crash
        logger.warning(
            "slack resolve_user_name(%s) failed (need users:read scope?): %r",
            user_id, e,
        )
        return None


def list_users(surface_id: str, *, client=None) -> list[dict]:
    """Return ``[{"id", "name"}]`` for every (non-bot, non-deleted) member.

    Backs ``addressbook sync-slack``. Paginates ``users.list`` via the
    ``next_cursor``. REQUIRES ``users:read``; on ``missing_scope`` (or any
    error) returns ``[]`` so the sync degrades to a no-op instead of crashing.
    ``client`` is a test seam.
    """
    try:
        if client is None:
            bot_token, _ = _load_tokens(surface_id)
            client = _web_client(bot_token)
        out: list[dict] = []
        cursor: Optional[str] = None
        while True:
            resp = client.users_list(cursor=cursor, limit=200)
            data = resp.data if hasattr(resp, "data") else resp
            for member in (data or {}).get("members") or []:
                if member.get("deleted") or member.get("is_bot"):
                    continue
                if member.get("id") == "USLACKBOT":
                    continue
                name = _display_name(member)
                if member.get("id") and name:
                    out.append({"id": member["id"], "name": name})
            cursor = (
                ((data or {}).get("response_metadata") or {}).get("next_cursor")
                or ""
            )
            if not cursor:
                break
        return out
    except Exception as e:  # noqa: BLE001 — missing_scope / network must not crash
        logger.warning(
            "slack list_users(%s) failed (need users:read scope?): %r",
            surface_id, e,
        )
        return []


def send_with_cc(
    surface_id: str,
    channel: str,
    text: str,
    *,
    sender_agent_id: str,
) -> list[dict]:
    """Send an agent's Slack message, attributed by agent name.

    Every agent posts through the SAME Slack app identity, so without
    attribution a reply reads as the app (or as whoever invoked the slash
    command). Prepend ``[BOT <agent>]: `` so it's clear which agent is
    speaking. Idempotent — text already carrying the marker isn't re-prefixed.

    (The former outbound mirror into @orchestrator's inbox was removed per
    operator request — agents are first-class on Slack now and the CC was just
    noise. Name kept for caller stability.)"""
    name = (sender_agent_id or "").lstrip("@") or "agent"
    if not text.lstrip().startswith("[BOT "):
        text = f"[BOT {name}]: {text}"
    return send_message(surface_id, channel, text)
