"""``telegram`` CLI entry point.

Polling lives in the ``metasphere-gateway`` systemd service; there is
no CLI poller. See ``metasphere.gateway.daemon`` and
``metasphere.telegram.poller.run_poll_iteration``.
"""

from __future__ import annotations

DESCRIPTION = "Send Telegram messages, upload documents, or run getMe."

USAGE = """\
Usage: metasphere telegram <command> [args...]

Commands:
  send "msg"                       Send a message to the saved chat id
                                   as the current METASPHERE_AGENT_ID.
  send "@<name>" "msg"             Send to a named contact from
                                   ~/.metasphere/ADDRESSBOOK.yaml.
  send "msg" --to <name>           Same, via flag form.
  send "msg" --chat-id <id>        Send to a specific numeric chat id.
  send - --to <name>               Read the body from stdin (no quoting).
  send --body-file PATH --to <name>
                                   Read the body verbatim from a file.
  getme                            Print bot info (sanity check).
  register-commands [-v]           Publish the slash-command manifest
                                   via setMyCommands.
  send-document <path> [--caption ...] [--filename ...] [--chat-id ...]
                                   Upload a file to the chat via
                                   sendDocument.
  groups <subcommand> ...          See `metasphere telegram groups
                                   --help` for thread/topic management.

Rich content (parens, bullets, backticks, $, quotes, newlines): pass the
body via stdin ("-") or --body-file to skip ALL shell quoting.
In positional text, literal \\n and \\r\\n sequences become line breaks;
double the backslash to send a literal \\n sequence.

Polling lives in the metasphere-gateway systemd service; there is no
CLI poller.

Group routing: this CLI only addresses private chats. To send to a
Telegram group, register it as a metasphere project with a topic and
use `metasphere msg send @<project> ...` (auto-mirrors), or use
`metasphere telegram groups send` for ad-hoc topic sends.
"""


import argparse
import json
import os
import sys
from typing import List, Optional

from metasphere import contacts as _contacts
from metasphere.telegram import api, archiver, commands


def _resolve_contact(name: str) -> Optional[int]:
    """Look up a named contact via the unified addressbook.

    Reads ``~/.metasphere/ADDRESSBOOK.yaml`` first; falls back to the
    legacy ``~/.metasphere/config/telegram_contacts.json`` (with a
    one-time deprecation WARN) if the new file is missing. Both code
    paths live in :mod:`metasphere.contacts`. Names are
    case-insensitive at lookup.
    """
    return _contacts.lookup_telegram(name)


def _reject_group_chat_id(chat_id: int) -> Optional[str]:
    """Defense-in-depth: refuse to send to a Telegram group.

    Telegram group chat ids are negative. The CLI must never address
    a group, even when the operator passed ``--chat-id`` explicitly:
    the morning-briefing leak (2026-05-01 08:00Z) showed that any
    group-routing path through this CLI is a leak vector. Group sends
    happen via the gateway / handler layer, not via this CLI.

    Returns an operator-facing error string, or ``None`` if the id is
    a private chat (positive).
    """
    if chat_id < 0:
        return (
            f"Error: refusing to send to group chat id {chat_id}. "
            f"`metasphere telegram send` only addresses private chats. "
            f"To reach a group, either register it as a metasphere "
            f"project with a telegram_topic and use "
            f"`metasphere msg send @<project> ...` (auto-mirrors), or "
            f"use `metasphere telegram groups send` for ad-hoc topic "
            f"sends. The guard exists because group sends through this "
            f"CLI have caused operator-facing leaks (PR #58, 2026-05-01)."
        )
    return None


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


def _own_telegram_surface_id(agent: str) -> str:
    """Map the calling agent id to the Telegram surface it should send as.

    Mirrors ``gateway.daemon._derive_telegram_target_agent`` in reverse:
    ``@orchestrator`` -> the legacy bare ``"telegram"`` surface (byte-
    identical default for single-bot installs); any other agent ->
    ``telegram-<agent>`` so it only ever sends through ITS OWN bot token,
    never falling back to a token that happens to be visible in the
    process env. On a shared single-install-multi-agent deployment,
    hardcoding ``"telegram"`` here let one agent's default send silently
    go out through a *different* agent's bot.
    """
    if agent == "@orchestrator":
        return "telegram"
    return f"telegram-{agent.lstrip('@')}"


def cmd_send(args: argparse.Namespace) -> int:
    from metasphere.cli._body import resolve_body

    # ``args.text`` is nargs='*', so it's always a list. Resolve the
    # positional shape — accepts either one text arg, the ``@<name> <text>``
    # shorthand pair, or a body sourced from stdin ("-") / --body-file.
    positionals: list[str] = list(args.text)
    body_file = getattr(args, "body_file", None)

    if body_file is not None:
        # Body comes from the file; positionals may carry ONLY an optional
        # @<name> recipient (the recipient otherwise comes from --to/--chat-id).
        parsed_to = None
        if positionals:
            if len(positionals) == 1 and positionals[0].startswith("@"):
                parsed_to = positionals[0][1:] or None
                if parsed_to is None:
                    print("Error: empty contact name (positional is a bare '@').",
                          file=sys.stderr)
                    return 2
            else:
                print(
                    "Error: with --body-file the only positional allowed is an "
                    "@<name> recipient — the body comes from the file. "
                    "Use --to/--chat-id otherwise.",
                    file=sys.stderr,
                )
                return 2
        try:
            text = resolve_body(None, body_file)
        except ValueError as e:
            print(f"Error: {e}.", file=sys.stderr)
            return 2
        except OSError as e:
            print(f"Error: cannot read --body-file: {e}", file=sys.stderr)
            return 2
    else:
        parsed_to, raw_text, err = _parse_send_positionals(positionals)
        if err is not None:
            print(err, file=sys.stderr)
            return 2
        try:
            # raw_text == "-" → read the body from stdin (zero shell quoting).
            text = resolve_body(raw_text, None)
        except ValueError as e:
            print(f"Error: {e}.", file=sys.stderr)
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
        chat_id = _contacts.default_telegram_chat_id()
    if chat_id is None:
        print(
            "Error: no chat id. Pass --chat-id, --to <name>, or set "
            "`default-recipient: <name>` in ~/.metasphere/ADDRESSBOOK.yaml.",
            file=sys.stderr,
        )
        return 2
    err = _reject_group_chat_id(chat_id)
    if err is not None:
        print(err, file=sys.stderr)
        return 2
    agent = os.environ.get("METASPHERE_AGENT_ID", "@orchestrator")
    if agent != "@orchestrator":
        text = f"[{agent.lstrip('@')}]\n\n{text}"
    if not os.environ.get("METASPHERE_SUPPRESS_TELEGRAM_DEPRECATION"):
        print(
            "[hint] `metasphere telegram send` is the legacy single-surface "
            "CLI; prefer `metasphere message send` for new code.",
            file=sys.stderr,
        )
    api.send_with_cc(chat_id, text, surface_id=_own_telegram_surface_id(agent))
    archiver.archive_outgoing(
        agent, text, chat_id, surface_id=_own_telegram_surface_id(agent)
    )
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
    chat_id = args.chat_id or _contacts.default_telegram_chat_id()
    if chat_id is None:
        print(
            "Error: no chat id. Pass --chat-id, or set "
            "`default-recipient: <name>` in ~/.metasphere/ADDRESSBOOK.yaml.",
            file=sys.stderr,
        )
        return 2
    err = _reject_group_chat_id(chat_id)
    if err is not None:
        print(err, file=sys.stderr)
        return 2
    if not os.path.exists(args.path):
        print(f"Error: file not found: {args.path}", file=sys.stderr)
        return 2
    agent = os.environ.get("METASPHERE_AGENT_ID", "@orchestrator")
    caption = args.caption
    if agent != "@orchestrator" and caption:
        caption = f"[{agent.lstrip('@')}] {caption}"
    resp = api.send_with_cc(
        chat_id, document_path=args.path, caption=caption, filename=args.filename,
        surface_id=_own_telegram_surface_id(agent),
    )
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
    # ``text`` is nargs='*' so we capture the ``@<name> <text>`` shorthand,
    # the bare ``<text>`` form, ``-`` (read body from stdin), and the
    # body-from-file case (recipient-only positional). cmd_send picks them
    # apart via ``_parse_send_positionals`` + the --body-file branch.
    p_send.add_argument("text", nargs="*",
                        help='message text, "@<name>" "<text>" for an '
                             'addressbook lookup, or "-" to read the body '
                             "from stdin")
    p_send.add_argument("--chat-id", type=int, default=None,
                        help="numeric Telegram chat ID")
    p_send.add_argument("--to", default=None,
                        help="named contact from ~/.metasphere/ADDRESSBOOK.yaml")
    from metasphere.cli._body import add_body_file_arg
    add_body_file_arg(p_send)
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
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list and args_list[0] in ("--help", "-h"):
        sys.stdout.write(USAGE)
        return 0
    if args_list and args_list[0] == "groups":
        from metasphere.cli import telegram_groups
        return telegram_groups.main(args_list[1:])
    parser = build_parser()
    args = parser.parse_args(args_list)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
