"""``telegram`` CLI entry point.

Subcommands:
    telegram send "msg"        Send a message to the saved chat id as
                               the current ``METASPHERE_AGENT_ID``.
    telegram send "@<name>" "msg"
                               Send to a named contact from
                               ``~/.metasphere/ADDRESSBOOK.yaml``.
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

from metasphere import contacts as _contacts
from metasphere.io import atomic_write_text
from metasphere.telegram import api, archiver, commands

# Path order matters: rewrite-specific file first, then the canonical
# chat-id file. Falling back to the canonical chat id keeps
# `metasphere-telegram send "..."` working without --chat-id and without the
# user having to /start the bot a second time.
CHAT_ID_FILE = os.path.expanduser("~/.metasphere/config/telegram_chat_id_rewrite")
CHAT_ID_FILE_CANONICAL = os.path.expanduser("~/.metasphere/config/telegram_chat_id")


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
    """Look up a named contact via the unified addressbook.

    Reads ``~/.metasphere/ADDRESSBOOK.yaml`` first; falls back to the
    legacy ``~/.metasphere/config/telegram_contacts.json`` (with a
    one-time deprecation WARN) if the new file is missing. Both code
    paths live in :mod:`metasphere.contacts`. Names are
    case-insensitive at lookup.
    """
    return _contacts.lookup_telegram(name)


def _save_chat_id(chat_id: int) -> None:
    atomic_write_text(CHAT_ID_FILE, str(chat_id))


def _parse_send_positionals(positionals: list[str]) -> tuple[Optional[str], Optional[str], Optional[str]]:
    """Resolve send-positional shapes into ``(to, text, error_msg)``.

    Accepted shapes:
      ["msg"]                  → (None, "msg", None)
      ["@<name>", "msg"]       → ("<name>", "msg", None)

    Anything else is an error. The returned ``error_msg`` is the
    full operator-facing string to print on stderr; on success it
    is ``None``.

    The ``@<name>`` shorthand exists because agents naturally reach
    for ``metasphere telegram send "@<name>" "msg"`` thinking
    ``@<name>`` is a recipient. Pre-2026-04-30 this errored with
    "unrecognized arguments: msg" silently — agents that appended
    ``; echo "sent:1"`` for self-confirmation got success-confirmation
    even though nothing landed. Detect the shape, route correctly.
    """
    n = len(positionals)
    if n == 0:
        return (None, None, "Error: no message text provided.")

    first = positionals[0]
    if first.startswith("@"):
        name = first[1:]
        if not name:
            return (None, None,
                    "Error: empty contact name (positional starts with bare '@').\n"
                    "Usage: metasphere telegram send \"@<name>\" \"<text>\"")
        if n == 1:
            return (None, None,
                    f"Error: contact '@{name}' given but no message text. "
                    f"Usage: metasphere telegram send \"@{name}\" \"<text>\"")
        if n > 2:
            return (None, None,
                    f"Error: too many positionals after '@{name}' "
                    f"(got {n - 1} text args; expected 1 quoted string). "
                    f"Did you mean: metasphere telegram send "
                    f"\"@{name}\" \"<text>\"")
        return (name, positionals[1], None)

    # Non-@-prefixed first positional.
    if n > 1:
        return (None, None,
                f"Error: too many positionals (got {n}; expected 1).\n"
                f"Usage: metasphere telegram send "
                f"[--to <name> | --chat-id N | @<name>] \"<text>\". "
                f"Did you mean: metasphere telegram send "
                f"--to <name> \"{positionals[0]}\"?")
    return (None, positionals[0], None)


def cmd_send(args: argparse.Namespace) -> int:
    # ``args.text`` is nargs='+', so it's always a list. Resolve the
    # positional shape — accepts either one text arg or the
    # ``@<name> <text>`` shorthand pair. Anything else is an error.
    positionals: list[str] = list(args.text)
    parsed_to, text, err = _parse_send_positionals(positionals)
    if err is not None:
        print(err, file=sys.stderr)
        return 2

    # If the @<name> shorthand resolved a name, it overrides --to.
    if parsed_to is not None:
        args.to = parsed_to

    chat_id = args.chat_id
    if chat_id is None and getattr(args, "to", None):
        chat_id = _resolve_contact(args.to)
        if chat_id is None:
            ab_path = os.path.expanduser("~/.metasphere/ADDRESSBOOK.yaml")
            # Distinguish "contact missing entirely" from "contact
            # exists but has no telegram method".
            if _contacts.has_contact(args.to):
                print(
                    f"Error: contact '{args.to}' in {ab_path} has no "
                    f"telegram entry. Add: contacts.{args.to}.telegram: "
                    f"<chat_id>",
                    file=sys.stderr,
                )
            else:
                print(
                    f"Error: contact '{args.to}' not in {ab_path}. "
                    f"Add the entry to send.",
                    file=sys.stderr,
                )
            return 2
    if chat_id is None:
        chat_id = _load_chat_id()
    if chat_id is None:
        print("Error: no chat id. Pass --chat-id, --to, or have the user /start the bot first.", file=sys.stderr)
        return 2
    agent = os.environ.get("METASPHERE_AGENT_ID", "@orchestrator")
    if agent != "@orchestrator":
        text = f"[{agent.lstrip('@')}]\n\n{text}"
    api.send_with_cc(chat_id, text)
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
    resp = api.send_with_cc(chat_id, document_path=args.path, caption=caption, filename=args.filename)
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
    # ``text`` is nargs='+' so we capture the ``@<name> <text>``
    # shorthand as well as the bare ``<text>`` form. cmd_send picks
    # them apart in ``_parse_send_positionals``.
    p_send.add_argument("text", nargs="+",
                        help='message text, or "@<name>" "<text>" '
                             "for an addressbook lookup")
    p_send.add_argument("--chat-id", type=int, default=None,
                        help="numeric Telegram chat ID")
    p_send.add_argument("--to", default=None,
                        help="named contact from ~/.metasphere/ADDRESSBOOK.yaml")
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
