"""``telegram`` CLI entry point.

Subcommands:
    telegram send "msg"        Send a message to the saved chat id as
                               the current ``METASPHERE_AGENT_ID``.
    telegram getme             Print bot info (sanity check).
    telegram register-commands Publish slash-command manifest.
    telegram send-document     Upload a file via sendDocument.

Polling lives in the ``metasphere-gateway`` systemd service; there is
no CLI poller. See ``metasphere.gateway.daemon`` and
``metasphere.telegram.poller.run_poll_iteration``.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import List, Optional

from metasphere.io import atomic_write_text
from metasphere.telegram import api, archiver, commands

# Path order matters: rewrite-specific file first, then the canonical
# chat-id file. Falling back to the canonical chat id keeps
# `metasphere-telegram send "..."` working without --chat-id and without the
# user having to /start the bot a second time.
CHAT_ID_FILE = os.path.expanduser("~/.metasphere/config/telegram_chat_id_rewrite")
CHAT_ID_FILE_CANONICAL = os.path.expanduser("~/.metasphere/config/telegram_chat_id")


CONTACTS_FILE = os.path.expanduser("~/.metasphere/config/telegram_contacts.json")


def _load_chat_id() -> Optional[int]:
    for path in (CHAT_ID_FILE, CHAT_ID_FILE_CANONICAL):
        if not os.path.exists(path):
            continue
        try:
            with open(path) as f:
                value = f.read().strip()
            if value:
                return int(value)
        except (OSError, ValueError):
            continue
    return None


def _resolve_contact(name: str) -> Optional[int]:
    """Look up a named contact from telegram_contacts.json.

    File format: ``{"ella": 5418799462, "julian": 228838013, ...}``
    Names are case-insensitive.
    """
    if not os.path.exists(CONTACTS_FILE):
        return None
    try:
        with open(CONTACTS_FILE) as f:
            contacts = json.load(f)
        return contacts.get(name.lower())
    except (OSError, ValueError, json.JSONDecodeError):
        return None


def _save_chat_id(chat_id: int) -> None:
    atomic_write_text(CHAT_ID_FILE, str(chat_id))


def cmd_send(args: argparse.Namespace) -> int:
    chat_id = args.chat_id
    if chat_id is None and getattr(args, "to", None):
        chat_id = _resolve_contact(args.to)
        if chat_id is None:
            print(f"Error: unknown contact '{args.to}'. Add to {CONTACTS_FILE}", file=sys.stderr)
            return 2
    if chat_id is None:
        chat_id = _load_chat_id()
    if chat_id is None:
        print("Error: no chat id. Pass --chat-id, --to, or have the user /start the bot first.", file=sys.stderr)
        return 2
    agent = os.environ.get("METASPHERE_AGENT_ID", "@orchestrator")
    text = args.text
    if agent != "@orchestrator":
        text = f"[{agent.lstrip('@')}]\n\n{text}"
    api.send_message(chat_id, text)
    archiver.archive_outgoing(agent, text, chat_id)
    # Suppress the next Stop-hook auto-forward of the assistant text:
    # the user already got this content explicitly. Without this, every
    # turn that calls `metasphere-telegram send` produces a duplicate
    # message in chat (the explicit send + the posthook recap).
    if agent == "@orchestrator":
        try:
            from metasphere import paths as _paths
            from metasphere.posthook import mark_orchestrator_explicit_send

            mark_orchestrator_explicit_send(_paths.resolve())
        except Exception:  # noqa: BLE001 — never break send on a marker failure
            pass
    print(f"Sent to {chat_id} via {agent}")
    return 0


def cmd_getme(args: argparse.Namespace) -> int:
    print(json.dumps(api.get_me(), indent=2))
    return 0


def cmd_register_commands(args: argparse.Namespace) -> int:
    """Publish the bot's slash-command manifest via setMyCommands."""
    resp = commands.register_bot_commands()
    published = [c for c, _ in commands.BOT_COMMANDS_MANIFEST]
    print(f"Registered {len(published)} commands: {', '.join('/' + c for c in published)}")
    if args.verbose:
        print(json.dumps(resp, indent=2))
    return 0


def cmd_send_document(args: argparse.Namespace) -> int:
    chat_id = args.chat_id or _load_chat_id()
    if chat_id is None:
        print("Error: no chat id. Pass --chat-id or have the user /start the bot first.", file=sys.stderr)
        return 2
    if not os.path.exists(args.path):
        print(f"Error: file not found: {args.path}", file=sys.stderr)
        return 2
    agent = os.environ.get("METASPHERE_AGENT_ID", "@orchestrator")
    caption = args.caption
    if agent != "@orchestrator" and caption:
        caption = f"[{agent.lstrip('@')}] {caption}"
    resp = api.send_document(chat_id, args.path, caption=caption, filename=args.filename)
    # Same dedupe-marker treatment as text sends — the user already got the
    # file, so the Stop hook should not also forward the assistant text.
    if agent == "@orchestrator":
        try:
            from metasphere import paths as _paths
            from metasphere.posthook import mark_orchestrator_explicit_send

            mark_orchestrator_explicit_send(_paths.resolve())
        except Exception:  # noqa: BLE001
            pass
    print(f"Sent {args.path} to {chat_id} via {agent} (file_id={resp.get('result',{}).get('document',{}).get('file_id','?')})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="telegram", description="metasphere telegram CLI (rewrite)")
    sub = p.add_subparsers(dest="cmd", required=True)

    # ``telegram poll`` and ``telegram once`` were removed. Production
    # polling is the metasphere-gateway systemd service; ad-hoc
    # introspection of what the poller is doing goes via the debug log
    # at ~/.metasphere/state/telegram_debug.log (see poller.py).
    p_send = sub.add_parser("send", help="send a message")
    p_send.add_argument("text")
    p_send.add_argument("--chat-id", type=int, default=None,
                        help="numeric Telegram chat ID")
    p_send.add_argument("--to", default=None,
                        help="named contact from ~/.metasphere/config/telegram_contacts.json")
    p_send.set_defaults(func=cmd_send)

    p_me = sub.add_parser("getme", help="print bot info")
    p_me.set_defaults(func=cmd_getme)

    p_reg = sub.add_parser("register-commands",
                           help="publish slash-command manifest via setMyCommands")
    p_reg.add_argument("-v", "--verbose", action="store_true")
    p_reg.set_defaults(func=cmd_register_commands)

    p_doc = sub.add_parser("send-document", help="upload a file to the chat via sendDocument")
    p_doc.add_argument("path", help="local path to the file")
    p_doc.add_argument("--caption", default=None, help="optional caption shown beneath the file")
    p_doc.add_argument("--filename", default=None, help="override the displayed filename")
    p_doc.add_argument("--chat-id", type=int, default=None)
    p_doc.set_defaults(func=cmd_send_document)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
