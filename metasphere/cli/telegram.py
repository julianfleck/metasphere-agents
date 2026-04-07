"""``telegram`` CLI entry point.

Subcommands:
    telegram poll              Run the long-poll loop forever (daemon).
    telegram once              Single getUpdates call; process and exit.
    telegram send "msg"        Send a message to the saved chat id as
                               the current ``METASPHERE_AGENT_ID``.
    telegram getme             Print bot info (sanity check).

This CLI talks to the REWRITE bot only — see metasphere.telegram.api.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from metasphere.telegram import api, archiver, commands, inject, poller

CHAT_ID_FILE = os.path.expanduser("~/.metasphere/config/telegram_chat_id_rewrite")


def _load_chat_id() -> Optional[int]:
    if not os.path.exists(CHAT_ID_FILE):
        return None
    try:
        with open(CHAT_ID_FILE) as f:
            return int(f.read().strip())
    except Exception:
        return None


def _save_chat_id(chat_id: int) -> None:
    os.makedirs(os.path.dirname(CHAT_ID_FILE), exist_ok=True)
    with open(CHAT_ID_FILE, "w") as f:
        f.write(str(chat_id))


def _handle_update(u: poller.Update) -> None:
    if not u.text or u.chat_id is None:
        return
    # Save chat id (DMs only — forum threads have thread_id)
    if not u.thread_id:
        _save_chat_id(u.chat_id)

    archiver.archive_message(u.raw.get("message") or u.raw)
    archiver.save_latest(u.raw.get("message") or u.raw)

    ctx = commands.Context(
        chat_id=u.chat_id, from_user=u.from_username or "?", thread_id=u.thread_id
    )

    if u.text.startswith("/"):
        reply = commands.dispatch(u.text, ctx)
        if reply:
            api.send_message(u.chat_id, reply, message_thread_id=u.thread_id)
    else:
        # Inject into orchestrator tmux + acknowledge with reaction
        inject.submit_to_tmux(f"@{u.from_username}", u.text)
        if u.message_id:
            try:
                api.set_message_reaction(u.chat_id, u.message_id, "👀")
            except api.TelegramAPIError:
                pass


def cmd_poll(args: argparse.Namespace) -> int:
    print(f"[telegram] starting poll loop (timeout={args.timeout}s)", flush=True)
    for u in poller.poll(timeout=args.timeout):
        ts = u.date or 0
        print(f"[telegram] update={u.update_id} from=@{u.from_username}: {u.text!r}", flush=True)
        try:
            _handle_update(u)
        except Exception as e:
            print(f"[telegram] handler error: {e}", flush=True)
    return 0


def cmd_once(args: argparse.Namespace) -> int:
    offset = poller.load_offset()
    updates = poller.get_updates(offset=offset, timeout=1)
    for u in updates:
        _handle_update(u)
        poller.save_offset(u.update_id + 1)
    print(f"[telegram] processed {len(updates)} update(s)")
    return 0


def cmd_send(args: argparse.Namespace) -> int:
    chat_id = args.chat_id or _load_chat_id()
    if chat_id is None:
        print("Error: no chat id. Pass --chat-id or have the user /start the bot first.", file=sys.stderr)
        return 2
    agent = os.environ.get("METASPHERE_AGENT_ID", "@orchestrator")
    text = args.text
    if agent != "@orchestrator":
        text = f"[{agent.lstrip('@')}]\n\n{text}"
    api.send_message(chat_id, text)
    archiver.archive_outgoing(agent, text, chat_id)
    print(f"Sent to {chat_id} via {agent}")
    return 0


def cmd_getme(args: argparse.Namespace) -> int:
    print(json.dumps(api.get_me(), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="telegram", description="metasphere telegram CLI (rewrite)")
    sub = p.add_subparsers(dest="cmd", required=True)

    p_poll = sub.add_parser("poll", help="long-poll forever")
    p_poll.add_argument("--timeout", type=int, default=30)
    p_poll.set_defaults(func=cmd_poll)

    p_once = sub.add_parser("once", help="single getUpdates call")
    p_once.set_defaults(func=cmd_once)

    p_send = sub.add_parser("send", help="send a message")
    p_send.add_argument("text")
    p_send.add_argument("--chat-id", type=int, default=None)
    p_send.set_defaults(func=cmd_send)

    p_me = sub.add_parser("getme", help="print bot info")
    p_me.set_defaults(func=cmd_getme)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
