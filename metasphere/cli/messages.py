"""CLI shim mirroring the bash ``scripts/messages`` command surface.

Usage::

    python -m metasphere.cli.messages                          # unread inbox
    python -m metasphere.cli.messages all                      # all messages
    python -m metasphere.cli.messages send @target !label "msg"
    python -m metasphere.cli.messages reply <id> "response"
    python -m metasphere.cli.messages done <id> "note"
    python -m metasphere.cli.messages read <id>
    python -m metasphere.cli.messages tree
    python -m metasphere.cli.messages status [id]
"""

from __future__ import annotations

import sys
from pathlib import Path

from metasphere import messages as _msgs
from metasphere import paths as _paths
from metasphere.identity import resolve_agent_id


_STATUS_ICON = {
    _msgs.STATUS_UNREAD: "○",
    _msgs.STATUS_READ: "◐",
    _msgs.STATUS_REPLIED: "◑",
    _msgs.STATUS_COMPLETED: "●",
}


def _ctx():
    p = _paths.resolve()
    return p, resolve_agent_id(p)


def _print_inbox(show_all: bool) -> int:
    p, _agent = _ctx()
    msgs = _msgs.collect_inbox(p.scope, p.repo)
    unread = sum(1 for m in msgs if m.status == _msgs.STATUS_UNREAD)
    total = len(msgs)
    if total == 0:
        print("## Messages: No messages in scope")
        return 0
    print(f"## Messages ({unread} unread, {total} total)")
    print(f"## Scope: {_paths.rel_path(p.scope, p.repo)}")
    print()
    for m in msgs:
        if not show_all and m.status != _msgs.STATUS_UNREAD:
            continue
        icon = _STATUS_ICON.get(m.status, "?")
        reply = f" ↩ reply to {m.reply_to}" if m.reply_to else ""
        body_preview = " ".join(m.body.split())[:60]
        print(f"{icon} {m.label} from {m.from_} [{m.id}]{reply}")
        print(f"  {m.scope} | {m.created}")
        print(f"  {body_preview}")
        print()
    return 0


def _cmd_send(args: list[str]) -> int:
    if len(args) < 3:
        print('Usage: messages send @target !label "message"', file=sys.stderr)
        return 1
    target, label, *rest = args
    body = " ".join(rest)
    p, agent = _ctx()
    msg = _msgs.send_message(target, label, body, agent, paths=p)
    print(f"Sent {msg.id} to {target} ({msg.scope})")
    print(f"  Label: {label}")
    return 0


def _cmd_reply(args: list[str]) -> int:
    if len(args) < 2:
        print('Usage: messages reply <msg-id> "response"', file=sys.stderr)
        return 1
    orig, *rest = args
    body = " ".join(rest)
    p, agent = _ctx()
    msg = _msgs.reply_to_message(orig, body, agent, paths=p)
    print(f"Replied to {orig} → {msg.id}")
    return 0


def _cmd_done(args: list[str]) -> int:
    if not args:
        print('Usage: messages done <msg-id> ["note"]', file=sys.stderr)
        return 1
    orig, *rest = args
    note = " ".join(rest)
    p, agent = _ctx()
    reply = _msgs.mark_done(orig, note, agent, paths=p)
    if reply:
        print(f"Completed {orig}, notified → {reply.id}")
    else:
        print(f"Completed {orig}")
    return 0


def _cmd_read(args: list[str]) -> int:
    if not args:
        print("Usage: messages read <msg-id>", file=sys.stderr)
        return 1
    p, _ = _ctx()
    msg = _msgs.mark_read(args[0], paths=p)
    if msg.path:
        print(msg.path.read_text())
    return 0


def _cmd_tree(_args: list[str]) -> int:
    p, _ = _ctx()
    print("## Message Tree")
    print(f"## Scope: {_paths.rel_path(p.scope, p.repo)}")
    print()
    for msg_dir in sorted(Path(p.repo).rglob(".messages")):
        if not msg_dir.is_dir():
            continue
        scope_dir = msg_dir.parent
        inbox_count = sum(1 for _ in (msg_dir / "inbox").glob("*.msg")) if (msg_dir / "inbox").is_dir() else 0
        outbox_count = sum(1 for _ in (msg_dir / "outbox").glob("*.msg")) if (msg_dir / "outbox").is_dir() else 0
        if inbox_count == 0 and outbox_count == 0:
            continue
        marker = " ← you are here" if scope_dir.resolve() == p.scope.resolve() else ""
        print(f"{_paths.rel_path(scope_dir, p.repo)}{marker}")
        print(f"  inbox: {inbox_count} | outbox: {outbox_count}")
    return 0


def _cmd_status(args: list[str]) -> int:
    p, _ = _ctx()
    if not args:
        print("## Agent Status")
        agents_dir = p.agents
        if agents_dir.is_dir():
            for status_file in sorted(agents_dir.glob("*/status")):
                agent = status_file.parent.name
                print(f"- {agent}: {status_file.read_text().strip()}")
        return 0
    msg_id = args[0]
    path = _msgs._find_inbox_msg(msg_id, p.repo)
    if path is None:
        print(f"Message {msg_id} not found", file=sys.stderr)
        return 1
    m = _msgs.read_message(path)
    print(f"Message: {m.id}")
    print(f"Status: {m.status}")
    print(f"Created: {m.created}")
    print(f"Read: {m.read_at}")
    print(f"Replied: {m.replied_at}")
    print(f"Completed: {m.completed_at}")
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] in ("--help", "-h"):
        print(__doc__ or "")
        return 0
    if not argv:
        return _print_inbox(show_all=False)
    cmd, rest = argv[0], argv[1:]
    if cmd == "all":
        return _print_inbox(show_all=True)
    handlers = {
        "send": _cmd_send,
        "reply": _cmd_reply,
        "done": _cmd_done,
        "read": _cmd_read,
        "tree": _cmd_tree,
        "status": _cmd_status,
    }
    h = handlers.get(cmd)
    if not h:
        print(f"unknown command: {cmd}", file=sys.stderr)
        return 1
    return h(rest)


if __name__ == "__main__":
    raise SystemExit(main())
