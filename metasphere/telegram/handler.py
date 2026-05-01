"""Single source-of-truth handler for incoming Telegram updates.

Called by ``metasphere.telegram.poller.run_poll_iteration`` on every
update the gateway daemon sees. There is no other per-update path:
both the old CLI ``_handle_update`` wrapper and the ``metasphere
telegram poll/once`` subcommands have been removed (the gateway
systemd service is the single source of truth for inbound polling).
"""

from __future__ import annotations

import json
import os
from typing import Callable, Optional

from ..io import atomic_write_text
from ..paths import resolve as _resolve_paths
from . import api, archiver, attachments, commands, inject, poller


# Injectable hooks, mostly for tests. The defaults call the real
# production helpers; tests pass stubs to inspect side effects without
# monkeypatching module-globals.
Sender = Callable[..., object]
Reactor = Callable[..., object]
TmuxSubmit = Callable[..., bool]
ChatIdSaver = Callable[[int], None]
PendingAckWriter = Callable[[int, int], None]


def _default_save_chat_id(chat_id: int) -> None:
    """Persist the rewrite chat id to the canonical file.

    Kept here (not imported from cli/telegram.py) so the gateway path
    doesn't have to depend on the CLI module — avoids an import cycle.
    """
    path = os.path.expanduser("~/.metasphere/config/telegram_chat_id_rewrite")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        atomic_write_text(path, str(chat_id))
    except Exception:
        pass


def _is_addressed_to_bot(u: poller.Update, bot_username: str,
                         bot_id: Optional[int]) -> bool:
    """Return True iff the update should wake the agent.

    Restrictive only for ``chat_type in ("group", "supergroup")``.
    Private DMs and channels (and unknown chat types — e.g.,
    pre-2026-05-01 fixtures that don't set ``chat.type``) wake on
    every body-bearing inbound, preserving existing behavior. Group
    chatter, in contrast, only wakes when the message is explicitly
    addressed to the bot — privacy mode is OFF on the production
    bot so we receive every group message, and unaddressed lines
    must NOT clobber the orchestrator's tmux REPL.

    "Addressed" in a group means any of:
      - text starts with ``/`` (slash command, with or without
        ``@bot`` suffix).
      - reply_to_message authored by the bot itself.
      - explicit ``@<bot_username>`` mention entity in the message.

    ``bot_username`` should be lowercased; either may be falsy if
    ``getMe`` failed (we degrade to "no group wakes" — safer than
    waking on everything).
    """
    if u.chat_type not in ("group", "supergroup"):
        return True
    msg = u.raw.get("message") or u.raw.get("edited_message") or {}
    text = u.text or msg.get("caption") or ""
    if text.startswith("/"):
        return True
    if bot_id is not None and u.reply_to_from_id == bot_id:
        return True
    if not bot_username:
        return False
    target = "@" + bot_username
    for entity in (msg.get("entities") or msg.get("caption_entities") or []):
        if entity.get("type") != "mention":
            continue
        offset = entity.get("offset") or 0
        length = entity.get("length") or 0
        handle = text[offset:offset + length].lower()
        if handle == target:
            return True
    return False


def _default_pending_ack_writer(chat_id: int, message_id: int) -> None:
    """Stash ``{chat_id, message_id}`` so the posthook can flip 👀 → 👍
    once the orchestrator's reply lands. Best-effort.
    """
    try:
        paths = _resolve_paths()
        paths.state.mkdir(parents=True, exist_ok=True)
        (paths.state / "telegram_pending_ack.json").write_text(
            json.dumps({"chat_id": chat_id, "message_id": message_id})
        )
    except Exception:
        pass


def handle_update(
    u: poller.Update,
    *,
    sender: Optional[Sender] = None,
    reactor: Optional[Reactor] = None,
    tmux_submit: Optional[TmuxSubmit] = None,
    save_chat_id: Optional[ChatIdSaver] = None,
    write_pending_ack: Optional[PendingAckWriter] = None,
) -> None:
    """Process a single inbound Telegram update.

    Flow (union of what cli/telegram.py and gateway/daemon.py each did
    partially):

    1. Reaction → archive_reaction, return.
    2. No chat_id → early return.
    3. Parse attachments. Body = text or caption.
    4. Debug log post_parse.
    5. No body and no refs → early return.
    6. Save chat_id (non-forum DM only).
    7. Archive (try/except — must not block injection).
    8. Slash command → dispatch + reply, return.
    9. Download attachments, render block, append to payload.
    10. React 👀, stash pending_ack.
    11. Inject payload into the orchestrator tmux session.
    """
    sender = sender or api.send_message
    reactor = reactor or api.set_message_reaction
    tmux_submit = tmux_submit or inject.submit_to_tmux
    save_chat_id = save_chat_id or _default_save_chat_id
    write_pending_ack = write_pending_ack or _default_pending_ack_writer

    if u.kind == "reaction":
        if u.chat_id is None:
            return
        archiver.archive_reaction(
            target_message_id=u.reaction_target_message_id,
            emojis=u.reaction_emojis,
            from_username=u.from_username,
            chat_id=u.chat_id,
            date=u.date,
        )
        return

    if u.chat_id is None:
        attachments.debug_log({
            "stage": "early_return", "reason": "chat_id is None",
            "update_id": u.update_id,
        })
        return

    msg = u.raw.get("message") or u.raw.get("edited_message") or u.raw
    refs = attachments.parse_attachments(msg)
    # Photos/videos/documents carry a ``caption`` instead of ``text``.
    caption = msg.get("caption") or ""
    body = u.text or caption

    attachments.debug_log({
        "stage": "post_parse",
        "update_id": u.update_id,
        "summary": attachments.summarize_message_for_debug(msg),
        "refs": [
            {"kind": r.kind, "file_id": r.file_id, "file_size": r.file_size,
             "file_name": r.file_name, "mime_type": r.mime_type}
            for r in refs
        ],
        "body_source": "text" if u.text else ("caption" if caption else None),
        "body_len": len(body or ""),
    })

    if not body and not refs:
        attachments.debug_log({
            "stage": "early_return", "reason": "no body and no refs",
            "update_id": u.update_id,
        })
        return

    # Save chat id only on DMs (forum topics have thread_id).
    if not u.thread_id:
        try:
            save_chat_id(u.chat_id)
        except Exception:
            pass

    try:
        archiver.archive_message(msg)
        archiver.save_latest(msg)
    except Exception as e:
        attachments.debug_log({
            "stage": "archive_error",
            "update_id": u.update_id,
            "error": f"{type(e).__name__}: {e}",
        })

    ctx = commands.Context(
        chat_id=u.chat_id, from_user=u.from_username or "?", thread_id=u.thread_id
    )

    # Slash dispatch — key on u.text only (a caption starting with "/"
    # is not a command).
    if u.text and u.text.startswith("/"):
        reply = commands.dispatch(u.text, ctx)
        if reply:
            try:
                if isinstance(reply, commands.Reply):
                    sender(
                        u.chat_id, reply.text,
                        parse_mode=reply.parse_mode,
                        message_thread_id=u.thread_id,
                    )
                else:
                    sender(u.chat_id, reply, message_thread_id=u.thread_id)
            except Exception:
                pass
        return

    payload = body or ""
    downloaded: list = []
    if refs:
        downloaded = attachments.download_attachments(
            u.message_id or u.update_id, refs,
        )
        block = attachments.render_attachment_block(downloaded)
        payload = f"{payload}\n\n{block}".strip() if payload else block

    attachments.debug_log({
        "stage": "pre_inject",
        "update_id": u.update_id,
        "downloaded": [
            {"kind": d.kind,
             "path": str(d.path) if d.path else None,
             "file_size": d.file_size,
             "error": d.error}
            for d in downloaded
        ],
        "payload_preview": payload[:200],
    })

    # React 👀 + stash pending_ack BEFORE injecting so the user sees an
    # acknowledgement quickly, even if the tmux submit stalls.
    if u.message_id:
        try:
            reactor(u.chat_id, u.message_id, "👀")
        except Exception:
            pass
        try:
            write_pending_ack(u.chat_id, u.message_id)
        except Exception:
            pass

    # Wake gating: privacy mode is OFF on the production bot, so we
    # receive every group message — but only addressed ones should
    # clobber the orchestrator's tmux REPL. Unaddressed group chatter
    # stays in the stream/archive (already written above) and waits
    # for the next heartbeat tick to surface.
    bot = api.bot_identity()
    if not _is_addressed_to_bot(u, bot.get("username") or "", bot.get("id")):
        attachments.debug_log({
            "stage": "wake_skipped",
            "reason": "unaddressed group message",
            "update_id": u.update_id,
            "chat_type": u.chat_type,
        })
        return

    # Addressed inbound — the orchestrator's tmux session must be
    # alive when we inject, otherwise tmux_submit returns False and
    # the message sits in the archive until the next heartbeat tick
    # (the dormant-session 9-min-latency incident from 2026-04-30).
    # Idempotent start_session is a no-op when the session already
    # exists.
    try:
        from ..gateway.session import start_session as _start_session
        _start_session()
    except Exception as e:  # noqa: BLE001 — never break inject on session-create failure
        # Surface the failure in the debug log so the original
        # 9-min-latency bug doesn't recur silently when start_session
        # itself starts failing (tmux missing, disk full, etc.).
        attachments.debug_log({
            "stage": "session_start_failed",
            "update_id": u.update_id,
            "error": f"{type(e).__name__}: {e}",
        })

    # defer_if_busy=False: telegram-user inbound IS the user typing.
    # Clobbering visible REPL content with a telegram message is not a
    # race — it's precisely what the user wants. Only NON-user injectors
    # (heartbeat, scheduled cron, agent-to-agent wakes, restart-wake)
    # defer; this path must always land. PR #23 originally set this to
    # True and broke the user's telegram inbound when the orchestrator
    # pane had any visible typed content (12:15/12:48/12:54 CEST went
    # to /dev/null with status=read but no user-turn).
    tmux_submit(f"@{u.from_username or 'user'}", payload, defer_if_busy=False)
