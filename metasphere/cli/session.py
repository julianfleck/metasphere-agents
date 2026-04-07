"""CLI: ``python -m metasphere.cli.session``.

    session list
    session info <name|@agent>
    session attach <name|@agent>
"""

from __future__ import annotations

import sys

from metasphere.session import attach_to, list_sessions, session_info


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    cmd, *rest = args

    if cmd in ("list", "ls"):
        rows = list_sessions()
        if not rows:
            print("(no metasphere sessions)")
            return 0
        for s in rows:
            mark = "*" if s.attached else " "
            print(f"{mark} {s.agent:24} {s.name:32} windows={s.windows}")
        return 0

    if cmd == "info":
        if not rest:
            print("usage: session info <name|@agent>", file=sys.stderr)
            return 2
        s = session_info(rest[0])
        if not s:
            print(f"no session: {rest[0]}", file=sys.stderr)
            return 1
        print(f"name:     {s.name}")
        print(f"agent:    {s.agent}")
        print(f"windows:  {s.windows}")
        print(f"created:  {s.created}")
        print(f"attached: {s.attached}")
        return 0

    if cmd == "attach":
        if not rest:
            print("usage: session attach <name|@agent>", file=sys.stderr)
            return 2
        return attach_to(rest[0])

    print(f"unknown subcommand: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
