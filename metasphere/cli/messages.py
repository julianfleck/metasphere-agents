"""``metasphere msg`` — cross-agent message bus CLI.

Operator-facing surface over ``metasphere.messages``: send/reply/done,
list inbox for a scope, render the reply graph. Messages are
persisted as per-agent files with a strict label vocabulary
(``!task``/``!info``/``!query``/``!reply``/``!done``/``!urgent``)
that the consolidate sweep keys off when deciding lifetime. Wake
behaviour is driven by message-label + recipient state, not by this
shim — sending a ``!task`` to a dormant agent does not implicitly
wake it.
"""

from __future__ import annotations

import sys
from pathlib import Path


DESCRIPTION = "Send, list, reply to, and resolve cross-agent messages."

USAGE = """\
Usage: metasphere msg [<command> [args...]]

With no arguments, prints unread inbox for the current scope. Commands:

  metasphere msg all                          List read + unread messages.
  metasphere msg send @target !label "msg"    Send a message.
  metasphere msg send @target !label -         Read body from stdin (no quoting).
  metasphere msg send @target !label --body-file PATH   Read body from a file.
  metasphere msg reply <id> "response"        Reply to <id>.
  metasphere msg reply <id> -                  Reply, body from stdin.
  metasphere msg reply <id> --body-file PATH   Reply, body from a file.
  metasphere msg done <id> "note"             Mark <id> resolved.
  metasphere msg done <id> --body-file PATH    Resolve, note from a file.
  metasphere msg read <id>                    Pretty-print one message.
  metasphere msg tree                         Render the reply graph.
  metasphere msg status [id]                  Show status of one message.

Identifiers `@target` resolve to agents (`@<name>`), users
(`@<handle>`), or projects (`@<project>`). Labels are bang-prefixed
(`!task`, `!info`, `!query`, `!done`, `!reply`, `!urgent`).
"""

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


_USAGE_HINTS = {
    "send": 'Use: metasphere msg send @target !label "message"',
    "reply": 'Use: metasphere msg reply <msg-id> "response"',
    "done": 'Use: metasphere msg done <msg-id> ["note"]',
    "read": "Use: metasphere msg read <msg-id>",
    "status": "Use: metasphere msg status <msg-id>",
}


def _reject_flag_shape(value: str, role: str, op: str) -> int | None:
    """Return rc=1 + print error if ``value`` looks like a leaked CLI flag.

    Catches both ``--foo`` and ``-x``: msg-ids, targets, and labels
    never legitimately start with ``-``. Same shape as the rejects in
    ``agents._validate_agent_name`` / ``project._validate_name`` —
    centralized here because ``msg`` commands use bare positional
    parsing (no argparse) and have to gate manually.
    """
    if value.startswith("-"):
        hint = _USAGE_HINTS.get(op, "")
        msg = (
            f"Error: {role} {value!r} looks like a flag — `msg {op}` "
            "takes positional args only."
        )
        if hint:
            msg = f"{msg} {hint}"
        print(msg, file=sys.stderr)
        return 1
    return None


def _print_inbox(show_all: bool) -> int:
    p, _agent = _ctx()
    msgs = _msgs.collect_inbox(p.scope, p.repo, view=True)
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


def _resolve_body_arg(
    rest: list[str], *, usage: str, allow_empty: bool = False,
) -> tuple[str | None, int | None]:
    """Resolve a message body from a command's trailing args.

    Shared by ``send`` / ``reply`` / ``done`` so all three accept the
    SAME body sources (parity): ``--body-file PATH`` reads a file, a lone
    positional ``-`` reads stdin, otherwise the remaining positionals are
    joined as inline text. This is the single source of truth — keeping
    ``reply``/``done`` on this path is what stops them from silently
    shipping a leaked ``--body-file``/``-`` token AS the body (the
    content-loss bug: the flag string landed in the message and the real
    content was dropped, with no error).

    Returns ``(body, None)`` on success, or ``(None, rc)`` after printing
    an error to stderr.
    """
    from metasphere.cli._body import STDIN_SENTINEL, resolve_body

    body_file: str | None = None
    text_arg: str | None = None
    if rest[:1] == ["--body-file"]:
        if len(rest) != 2:
            print(usage, file=sys.stderr)
            return None, 1
        body_file = rest[1]
    elif rest == [STDIN_SENTINEL]:
        text_arg = STDIN_SENTINEL
    else:
        text_arg = " ".join(rest)
    try:
        body = resolve_body(text_arg, body_file, allow_empty=allow_empty)
    except ValueError as e:
        print(f"Error: {e}.", file=sys.stderr)
        return None, 1
    except OSError as e:
        print(f"Error: cannot read --body-file: {e}", file=sys.stderr)
        return None, 1
    return body, None


def _cmd_send(args: list[str]) -> int:
    if len(args) < 3:
        print('Usage: metasphere msg send @target !label "message"', file=sys.stderr)
        return 1
    target, label, *rest = args
    # Reject flag-shaped target / label up front. ``msg send`` is purely
    # positional — there are no named flags. Agents (and humans) coming
    # in from ``metasphere telegram send --to <name>`` (which DOES use
    # --to) confabulate the same shape here, and the silent
    # ``target, label, *rest = args`` unpack accepts it: the message
    # goes out with ``to: --to``, ``label: @whatever``, and the real
    # body buried in the rest. Hard-fail so the corruption can't
    # silently ship (witnessed 2026-05-05 across two agents in the
    # same morning). Single-dash (``-x``) is
    # rejected too — same confabulation risk, no legitimate target /
    # label ever starts with ``-``.
    rc = _reject_flag_shape(target, "target", "send")
    if rc is not None:
        return rc
    rc = _reject_flag_shape(label, "label", "send")
    if rc is not None:
        return rc
    # Body sources (rich content with zero shell quoting): `--body-file PATH`,
    # `-` as the lone body token to read from stdin, else the remaining
    # positionals joined. Shared with reply/done via _resolve_body_arg.
    body, rc = _resolve_body_arg(
        rest,
        usage='Usage: metasphere msg send @target !label --body-file PATH',
    )
    if rc is not None:
        return rc
    p, agent = _ctx()
    msg = _msgs.send_message(target, label, body, agent, paths=p)
    print(f"Sent {msg.id} to {target} ({msg.scope})")
    print(f"  Label: {label}")
    return 0


def _cmd_reply(args: list[str]) -> int:
    if len(args) < 2:
        print('Usage: metasphere msg reply <msg-id> "response"', file=sys.stderr)
        return 1
    orig, *rest = args
    rc = _reject_flag_shape(orig, "msg-id", "reply")
    if rc is not None:
        return rc
    # Parity with `msg send`: a reply may pull its body from --body-file /
    # stdin `-`, not just inline text. Before this, those tokens were
    # joined verbatim into the body — `reply <id> --body-file X` shipped
    # the literal string "--body-file X" and dropped the file content,
    # silently. Reply bodies are required (the arg-count gate already
    # ensures at least one token), so empty is rejected.
    body, rc = _resolve_body_arg(
        rest, usage='Usage: metasphere msg reply <msg-id> --body-file PATH',
    )
    if rc is not None:
        return rc
    p, agent = _ctx()
    try:
        msg = _msgs.reply_to_message(orig, body, agent, paths=p)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1
    print(f"Replied to {orig} → {msg.id}")
    return 0


def _cmd_done(args: list[str]) -> int:
    if not args:
        print('Usage: metasphere msg done <msg-id> ["note"]', file=sys.stderr)
        return 1
    orig, *rest = args
    rc = _reject_flag_shape(orig, "msg-id", "done")
    if rc is not None:
        return rc
    # Parity with `msg send`: a done-note may come from --body-file /
    # stdin `-`. The note is OPTIONAL (`msg done <id>` with no note is
    # valid), so empty is allowed here — unlike reply/send.
    note, rc = _resolve_body_arg(
        rest, usage='Usage: metasphere msg done <msg-id> --body-file PATH',
        allow_empty=True,
    )
    if rc is not None:
        return rc
    p, agent = _ctx()
    try:
        reply = _msgs.mark_done(orig, note, agent, paths=p)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1
    if reply:
        print(f"Completed {orig}, notified → {reply.id}")
    else:
        print(f"Completed {orig}")
    return 0


def _cmd_read(args: list[str]) -> int:
    if not args:
        print("Usage: metasphere msg read <msg-id>", file=sys.stderr)
        return 1
    rc = _reject_flag_shape(args[0], "msg-id", "read")
    if rc is not None:
        return rc
    p, _ = _ctx()
    try:
        msg = _msgs.mark_read(args[0], paths=p)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        return 1
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
    rc = _reject_flag_shape(msg_id, "msg-id", "status")
    if rc is not None:
        return rc
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
        sys.stdout.write(USAGE)
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
