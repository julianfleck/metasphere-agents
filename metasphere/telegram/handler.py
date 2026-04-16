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

    # defer_if_busy=True: telegram bot loop is auto-driven; if Julian
    # is typing directly into the attached orchestrator pane, defer
    # the inject rather than interleave (the 2026-04-16 bug).
    tmux_submit(f"@{u.from_username or 'user'}", payload, defer_if_busy=True)
