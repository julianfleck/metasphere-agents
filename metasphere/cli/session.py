"""CLI: ``metasphere session``.

    session list
    session info <@agent>
    session attach <@agent>
    session stop <@agent>
    session restart <@agent> [reason]
    session send <@agent> <message>
"""

from __future__ import annotations

import sys

from metasphere.session import (
    attach_to,
    list_sessions,
    restart_session,
    send_to_session,
    session_info,
    stop_session,
)


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] in ("--help", "-h"):
        print(__doc__ or "")
        return 0
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
            mark = "●" if s.attached else "○"
            print(f"{mark} {s.agent:24} {s.name:32} windows={s.windows}")
        return 0

    if cmd == "info":
        if not rest:
            print("usage: session info <@agent>", file=sys.stderr)
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
            print("usage: session attach <@agent>", file=sys.stderr)
            return 2
        return attach_to(rest[0])

    if cmd == "stop":
        if not rest:
            print("usage: session stop <@agent>", file=sys.stderr)
            return 2
        ok = stop_session(rest[0])
        if ok:
            print(f"stopped {rest[0]}")
        else:
            print(f"no session for {rest[0]}", file=sys.stderr)
            return 1
        return 0

    if cmd == "restart":
        if not rest:
            print("usage: session restart <@agent> [reason]", file=sys.stderr)
            return 2
        agent = rest[0]
        reason = " ".join(rest[1:]) if len(rest) > 1 else "CLI restart"
        ok = restart_session(agent, reason)
        if ok:
            print(f"restarting {agent}: {reason}")
        else:
            print(f"no session for {agent}", file=sys.stderr)
            return 1
        return 0

    if cmd == "send":
        if len(rest) < 2:
            print("usage: session send <@agent> <message>", file=sys.stderr)
            return 2
        agent = rest[0]
        message = " ".join(rest[1:])
        ok = send_to_session(agent, message)
        if ok:
            print(f"sent to {agent}")
        else:
            print(f"no session for {agent}", file=sys.stderr)
            return 1
        return 0

    print(f"unknown subcommand: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
