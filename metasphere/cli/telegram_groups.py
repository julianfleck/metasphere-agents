"""CLI: ``python -m metasphere.cli.telegram_groups``.

    telegram-groups create <name>
    telegram-groups list
    telegram-groups send <topic> <text>
    telegram-groups link <topic>
"""

from __future__ import annotations

import sys

from metasphere.paths import resolve
from metasphere.telegram.groups import (
    create_topic,
    list_topics,
    send_to_topic,
    topic_link,
)


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    cmd, *rest = args
    paths = resolve()

    try:
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


if __name__ == "__main__":
    raise SystemExit(main())
