"""``slack`` CLI — Slack-specific convenience send.

Most workflows should prefer ``metasphere message send --surface slack-...``
(the cross-surface CLI from PR1). This module exists so operators
debugging a single Slack bot have a shorthand without having to type
the full ``message send`` flag set.
"""

from __future__ import annotations

import argparse
import os
import sys
from typing import Optional


DESCRIPTION = "Send Slack messages via a configured Slack surface."

USAGE = """\
Usage: metasphere slack send "<text>" [--surface <id>] [--channel <c>]
       metasphere slack send - --channel <c> < body.txt
       metasphere slack send --body-file body.txt --channel <c>

Options:
  --surface <id>    Slack surface id (e.g. slack-relay, slack-cluster-1).
                    Default: slack (legacy single-bot default).
  --channel <c>     Slack channel id (e.g. C012XYZ).
  --body-file PATH  Read the body verbatim from a file (no shell quoting).

For rich content — parens, bullets (•), backticks, $, quotes, newlines —
pass the body via stdin (positional "-") or --body-file to avoid ALL shell
escaping. For full cross-surface routing use `metasphere message send`.
"""


def cmd_send(args: argparse.Namespace) -> int:
    from metasphere.cli._body import resolve_body

    try:
        text = resolve_body(args.text, args.body_file)
    except ValueError as e:
        print(f"Error: {e}.", file=sys.stderr)
        return 2
    except OSError as e:
        print(f"Error: cannot read --body-file: {e}", file=sys.stderr)
        return 2
    surface_id: str = args.surface
    channel: Optional[str] = args.channel
    if not channel:
        print(
            "Error: --channel <id> required for `metasphere slack send`. "
            "For addressbook-driven sends use `metasphere message send "
            "--surface <id> --to <name>`.",
            file=sys.stderr,
        )
        return 2

    sender = os.environ.get("METASPHERE_AGENT_ID", "@orchestrator")
    try:
        from metasphere.slack import api as _slack_api
        _slack_api.send_with_cc(
            surface_id, channel, text, sender_agent_id=sender,
        )
    except FileNotFoundError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2
    print(f"Sent to {channel} via {sender} (surface={surface_id})")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="slack", description="metasphere slack CLI (single-surface send)"
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    p_send = sub.add_parser("send", help="send a message to a Slack channel")
    p_send.add_argument("text", nargs="?", default=None,
                        help='message text (or "-" to read the body from stdin)')
    p_send.add_argument("--surface", default="slack",
                        help='Slack surface_id (default: "slack")')
    p_send.add_argument("--channel", default=None,
                        help="Slack channel id, e.g. C012XYZ")
    from metasphere.cli._body import add_body_file_arg
    add_body_file_arg(p_send)
    p_send.set_defaults(func=cmd_send)
    return p


def main(argv: Optional[list[str]] = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list and args_list[0] in ("--help", "-h"):
        sys.stdout.write(USAGE)
        return 0
    parser = build_parser()
    args = parser.parse_args(args_list)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
