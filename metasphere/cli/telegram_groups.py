"""``metasphere telegram groups`` — Telegram forum + topic management.

Wraps ``metasphere.telegram.groups`` so an operator can register the
forum supergroup, list/create per-project topics, and reconcile the
local topic-id mapping with what Telegram actually owns. Topic ids are
the join key between project scope and Telegram message routing in the
gateway, so this is the canonical surface for rewiring that mapping
without touching the gateway state files by hand.
"""

from __future__ import annotations


DESCRIPTION = "Manage the Telegram forum supergroup + per-topic threads."

USAGE = """\
Usage: metasphere telegram groups <command> [args...]

Commands:
  setup [--forum-id <id>] [--force]
                          Register an existing forum supergroup the
                          bot is already an admin of. --forum-id sets
                          the id non-interactively; --force overwrites.
  verify [--forum-id <id>]
                          Check forum metadata + bot admin status.
  create <name>           Create a new topic in the registered forum.
  list                    List existing topics (id + name).
  send <topic> <text>     Post <text> into <topic>.
  link <topic>            Print the canonical URL for <topic>.

Telegram bots cannot create supergroups or enable Topics — that
step is reserved for a human user. The bot CAN create individual
topics inside a forum supergroup via createForumTopic, so `setup`
registers an existing supergroup the bot has been added to as an
admin with 'Manage Topics' permission.
"""

import os
import sys

from metasphere.paths import resolve
from metasphere.telegram.groups import (
    create_topic,
    get_forum_id,
    list_topics,
    send_to_topic,
    setup_forum,
    topic_link,
    verify_forum,
)


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] in ("--help", "-h"):
        sys.stdout.write(USAGE)
        return 0
    if not args:
        sys.stderr.write(USAGE)
        return 2
    cmd, *rest = args
    paths = resolve()

    try:
        if cmd == "setup":
            return _cmd_setup(rest, paths)

        if cmd == "verify":
            return _cmd_verify(rest, paths)

        if cmd in ("create", "new"):
            if rest and rest[0] in ("--help", "-h"):
                print("usage: create <name>", file=sys.stdout)
                return 0
            if not rest:
                print("usage: create <name>", file=sys.stderr)
                return 2
            t = create_topic(rest[0], paths=paths)
            print(f"{t.id}\t{t.name}")
            return 0

        if cmd in ("list", "ls"):
            if rest:
                head = rest[0]
                kind = "flag" if head.startswith("-") else "argument"
                sys.stderr.write(
                    f"metasphere telegram groups list: unexpected {kind}: {head}\n"
                    f"Usage: metasphere telegram groups list (takes no arguments)\n"
                )
                return 2
            for t in list_topics(paths=paths):
                print(f"{t.id}\t{t.name}")
            return 0

        if cmd in ("send", "msg"):
            if rest and rest[0] in ("--help", "-h"):
                print("usage: send <topic> <text>", file=sys.stdout)
                return 0
            if len(rest) < 2:
                print("usage: send <topic> <text>", file=sys.stderr)
                return 2
            send_to_topic(rest[0], " ".join(rest[1:]), paths=paths)
            print("ok")
            return 0

        if cmd in ("link", "url"):
            if rest and rest[0] in ("--help", "-h"):
                print("usage: link <topic>", file=sys.stdout)
                return 0
            if not rest:
                print("usage: link <topic>", file=sys.stderr)
                return 2
            print(topic_link(rest[0], paths=paths))
            return 0
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    except (RuntimeError, LookupError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1

    print(f"unknown subcommand: {cmd}", file=sys.stderr)
    return 2


def _parse_setup_args(rest: list[str]) -> tuple[str | None, bool, bool]:
    """Return (forum_id, force, interactive_fallback)."""
    forum_id: str | None = None
    force = False
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in ("--forum-id", "-f"):
            i += 1
            if i >= len(rest):
                raise ValueError("--forum-id requires a value")
            forum_id = rest[i]
        elif a.startswith("--forum-id="):
            forum_id = a.split("=", 1)[1]
        elif a == "--force":
            force = True
        elif a in ("--token", "-t"):
            i += 1
            if i >= len(rest):
                raise ValueError("--token requires a value")
            os.environ["TELEGRAM_BOT_TOKEN"] = rest[i]
        elif a.startswith("--token="):
            os.environ["TELEGRAM_BOT_TOKEN"] = a.split("=", 1)[1]
        else:
            raise ValueError(f"unknown flag: {a}")
        i += 1
    if forum_id is None:
        forum_id = os.environ.get("METASPHERE_FORUM_ID")
    interactive = forum_id is None
    return forum_id, force, interactive


def _cmd_setup(rest: list[str], paths) -> int:
    try:
        forum_id, force, interactive = _parse_setup_args(rest)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2

    if interactive:
        # Fallback wizard for humans on a TTY. The non-interactive path
        # (--forum-id / METASPHERE_FORUM_ID) is what the orchestrator uses.
        sys.stdout.write(
            "Telegram Forum Setup\n"
            "====================\n\n"
            "Telegram bots CANNOT create supergroups or enable topics —\n"
            "a human (you) must do that one-time step first:\n"
            "  1. Create a Telegram group\n"
            "  2. Group Settings → Topics → Enable\n"
            "  3. Add the bot as admin with 'Manage Topics' permission\n"
            "  4. Get the group id (e.g. via @userinfobot, starts with -100)\n\n"
        )
        try:
            forum_id = input("Enter Forum Group ID: ").strip()
        except EOFError:
            print(
                "error: no --forum-id provided and stdin is not a TTY. "
                "Pass --forum-id <id> or set METASPHERE_FORUM_ID.",
                file=sys.stderr,
            )
            return 2
        if not forum_id:
            print("error: no forum id given", file=sys.stderr)
            return 2

    try:
        status = setup_forum(forum_id, force=force, paths=paths)
    except RuntimeError as e:
        print(f"error: {e}", file=sys.stderr)
        return 1
    print(f"ok: forum {status.forum_id} ({status.title!r}) registered")
    if not status.ok:
        problem = status.describe_problem()
        print(f"warning: saved with --force despite: {problem}", file=sys.stderr)
    return 0


def _cmd_verify(rest: list[str], paths) -> int:
    forum_id: str | None = None
    i = 0
    while i < len(rest):
        a = rest[i]
        if a in ("--forum-id", "-f"):
            i += 1
            forum_id = rest[i] if i < len(rest) else None
        elif a.startswith("--forum-id="):
            forum_id = a.split("=", 1)[1]
        else:
            print(f"error: unknown flag: {a}", file=sys.stderr)
            return 2
        i += 1
    if forum_id is None:
        forum_id = os.environ.get("METASPHERE_FORUM_ID") or get_forum_id(paths)
    if not forum_id:
        print(
            "error: no forum id provided and none registered. "
            "Pass --forum-id <id> or run `metasphere telegram groups setup` first.",
            file=sys.stderr,
        )
        return 2
    status = verify_forum(forum_id, paths=paths)
    print(f"forum_id:        {status.forum_id}")
    print(f"title:           {status.title}")
    print(f"chat_type:       {status.chat_type}")
    print(f"is_forum:        {status.is_forum}")
    print(f"bot_is_admin:    {status.bot_is_admin}")
    print(f"can_manage_topics: {status.can_manage_topics}")
    if status.ok:
        print("status:          OK — ready to create topics")
        return 0
    print(f"status:          BROKEN — {status.describe_problem()}")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
