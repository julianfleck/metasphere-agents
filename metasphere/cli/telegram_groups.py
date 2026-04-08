"""CLI: ``python -m metasphere.cli.telegram_groups``.

    telegram-groups setup [--forum-id <id>] [--force]
    telegram-groups verify [--forum-id <id>]
    telegram-groups create <name>
    telegram-groups list
    telegram-groups send <topic> <text>
    telegram-groups link <topic>

NOTE on the Telegram bot limitation:
    Telegram bots CANNOT create supergroups or enable Topics — that step
    is reserved for a human user. A bot CAN create individual topics
    inside an already-existing forum supergroup via createForumTopic.
    The ``setup`` command therefore registers an *existing* forum group
    that you (a human) created, enabled Topics on, and added the bot to
    as an admin with the 'Manage Topics' permission. After that one-time
    human step, ``project topic create`` and friends are fully
    automatable.
"""

from __future__ import annotations

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
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    cmd, *rest = args
    paths = resolve()

    try:
        if cmd == "setup":
            return _cmd_setup(rest, paths)

        if cmd == "verify":
            return _cmd_verify(rest, paths)

        if cmd in ("create", "new"):
            if not rest:
                print("usage: create <name>", file=sys.stderr)
                return 2
            t = create_topic(rest[0], paths=paths)
            print(f"{t.id}\t{t.name}")
            return 0

        if cmd in ("list", "ls"):
            for t in list_topics(paths=paths):
                print(f"{t.id}\t{t.name}")
            return 0

        if cmd in ("send", "msg"):
            if len(rest) < 2:
                print("usage: send <topic> <text>", file=sys.stderr)
                return 2
            send_to_topic(rest[0], " ".join(rest[1:]), paths=paths)
            print("ok")
            return 0

        if cmd in ("link", "url"):
            if not rest:
                print("usage: link <topic>", file=sys.stderr)
                return 2
            print(topic_link(rest[0], paths=paths))
            return 0
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
