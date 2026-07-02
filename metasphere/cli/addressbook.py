"""``addressbook`` CLI — manage cross-surface contacts.

Today: ``sync-slack`` populates the standalone per-surface reverse map
(``surfaces.slack.<uid> -> name``) from a Slack workspace's user directory so
inbound Slack events render a friendly sender name instead of a raw uid.

REQUIRES the ``users:read`` bot-token scope. Without it Slack returns
``missing_scope``; ``sync-slack`` then reports 0 synced (no crash) and inbound
keeps falling back to the raw uid.
"""

from __future__ import annotations

import argparse
import sys
from typing import Optional


DESCRIPTION = "Manage cross-surface contacts (e.g. sync Slack user names)."

USAGE = """\
Usage: metasphere addressbook sync-slack [--surface <id>]

Subcommands:
  sync-slack   Populate surfaces.slack.<uid> -> name from the Slack user
               directory (users.list). Requires the users:read bot scope.

Options (sync-slack):
  --surface <id>   Slack surface id whose token to use (default: slack).

Notes:
  Slack contacts are PER-SURFACE and standalone (uid -> name), kept separate
  from telegram contacts — no unified identity. On missing_scope the sync is a
  no-op (0 synced) and inbound falls back to the raw uid.
"""


def cmd_sync_slack(args: argparse.Namespace) -> int:
    surface_id: str = args.surface
    try:
        from metasphere.slack import api as _slack_api
    except Exception as e:  # noqa: BLE001
        print(f"Error: slack adapter unavailable: {e}", file=sys.stderr)
        return 2

    members = _slack_api.list_users(surface_id)
    if not members:
        print(
            f"sync-slack: 0 users returned for surface {surface_id!r}. "
            "The bot token likely lacks the 'users:read' scope "
            "(add it + reinstall the app), or the workspace is empty. "
            "Inbound keeps falling back to the raw uid.",
            file=sys.stderr,
        )
        return 0

    from metasphere import contacts as _contacts

    surface_type = surface_id.partition("-")[0].strip().lower() or "slack"
    uid_to_name = {m["id"]: m["name"] for m in members if m.get("id") and m.get("name")}
    written = _contacts.set_surface_names(surface_type, uid_to_name)
    print(f"sync-slack: synced {written} slack contact(s) into surfaces.{surface_type}.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="addressbook", description="metasphere addressbook CLI"
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    p_sync = sub.add_parser(
        "sync-slack", help="populate slack uid -> name from users.list"
    )
    p_sync.add_argument(
        "--surface", default="slack",
        help='Slack surface id whose token to use (default: "slack")',
    )
    p_sync.set_defaults(func=cmd_sync_slack)
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
