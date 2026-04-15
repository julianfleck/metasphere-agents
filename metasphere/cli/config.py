"""``metasphere config telegram`` — bootstrap Telegram bot connectivity.

Writes the bot token and chat id to the canonical config files the
rest of the harness reads, then validates the token with a ``getMe``
round-trip and (optionally) discovers the chat id by polling for a
recent ``/start``.

Non-interactive::

    metasphere config telegram --token <token> --chat-id <id>

Interactive (the default when no flags are given): prompts for the
token, validates it, polls for recent senders, lets you pick one.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import List, Optional


CONFIG_DIR = Path.home() / ".metasphere" / "config"
TOKEN_ENV_FILE = CONFIG_DIR / "telegram.env"
CHAT_ID_FILE = CONFIG_DIR / "telegram_chat_id"


def _write_token(token: str) -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    # env-file format: KEY=value\n. Matches what api._read_env_file expects.
    TOKEN_ENV_FILE.write_text(f"TELEGRAM_BOT_TOKEN={token}\n")
    try:
        os.chmod(TOKEN_ENV_FILE, 0o600)  # tokens are secrets
    except OSError:
        pass
    return TOKEN_ENV_FILE


def _write_chat_id(chat_id: int) -> Path:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CHAT_ID_FILE.write_text(str(chat_id))
    return CHAT_ID_FILE


def _validate_token(token: str) -> tuple[bool, str]:
    """``getMe`` round-trip. Returns ``(ok, message)`` where ``message``
    is the bot's ``@username`` on success or a Telegram error string.
    """
    os.environ["TELEGRAM_BOT_TOKEN"] = token
    try:
        from ..telegram import api
        resp = api.get_me()
    except Exception as e:  # noqa: BLE001 — any failure → user-visible
        return False, f"getMe failed: {e}"
    result = resp.get("result") or {}
    username = result.get("username") or "?"
    return True, f"@{username}"


def _poll_for_chat_id(timeout: int = 30) -> List[dict]:
    """Single ``getUpdates`` call. Returns a list of candidate senders:
    ``[{"chat_id": ..., "name": ..., "last_text": ...}]``.
    """
    try:
        from ..telegram import api
        resp = api.call("getUpdates", timeout=timeout)
    except Exception:  # noqa: BLE001
        return []
    seen: dict[int, dict] = {}
    for upd in resp.get("result") or []:
        msg = upd.get("message") or upd.get("edited_message") or {}
        chat = msg.get("chat") or {}
        cid = chat.get("id")
        if cid is None:
            continue
        frm = msg.get("from") or {}
        name = chat.get("title") or frm.get("username") or frm.get("first_name") or "?"
        seen[cid] = {"chat_id": cid, "name": name, "last_text": msg.get("text") or ""}
    return list(seen.values())


def _prompt(msg: str) -> str:
    try:
        return input(msg).strip()
    except EOFError:
        return ""


def _interactive_flow() -> int:
    print("metasphere config telegram — interactive setup")
    print()
    print("1. Paste your bot token from @BotFather:")
    token = _prompt("   token: ")
    if not token:
        print("aborted: no token supplied", file=sys.stderr)
        return 2
    ok, msg = _validate_token(token)
    if not ok:
        print(f"token validation failed: {msg}", file=sys.stderr)
        return 2
    print(f"   bot identified as {msg}")
    _write_token(token)
    print(f"   token saved → {TOKEN_ENV_FILE}")
    print()
    print("2. Send '/start' to your bot in Telegram now, then press Enter.")
    _prompt("   [Enter when done]: ")
    candidates = _poll_for_chat_id()
    if not candidates:
        print("   no recent senders — falling back to manual entry.")
        manual = _prompt("   chat-id: ")
        if not manual.lstrip("-").isdigit():
            print("aborted: invalid chat-id", file=sys.stderr)
            return 2
        _write_chat_id(int(manual))
    elif len(candidates) == 1:
        cid = candidates[0]
        print(f"   found 1 sender: {cid['name']} (chat_id={cid['chat_id']})")
        _write_chat_id(cid["chat_id"])
    else:
        print(f"   found {len(candidates)} senders:")
        for i, c in enumerate(candidates, start=1):
            print(f"     [{i}] {c['name']} — chat_id={c['chat_id']}, "
                  f"last: {c['last_text'][:40]!r}")
        choice = _prompt("   pick one [1]: ") or "1"
        try:
            idx = int(choice) - 1
            picked = candidates[idx]
        except (ValueError, IndexError):
            print("aborted: bad selection", file=sys.stderr)
            return 2
        _write_chat_id(picked["chat_id"])
    print(f"   chat-id saved → {CHAT_ID_FILE}")
    print()
    print("3. Restart the gateway so it picks up the new config:")
    print("     metasphere daemon restart gateway")
    print()
    print("done.")
    return 0


def _noninteractive_flow(token: str, chat_id: Optional[int]) -> int:
    ok, msg = _validate_token(token)
    if not ok:
        print(f"token validation failed: {msg}", file=sys.stderr)
        return 2
    _write_token(token)
    print(f"token saved → {TOKEN_ENV_FILE} (bot: {msg})")
    if chat_id is not None:
        _write_chat_id(chat_id)
        print(f"chat-id saved → {CHAT_ID_FILE}")
    else:
        print("no --chat-id supplied; run interactively or pass one later.")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="metasphere config telegram",
        description="Wire up the Telegram bot token and chat id. "
        "Interactive if no flags are passed.",
    )
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_tg = sub.add_parser("telegram", help="Telegram bot setup.")
    p_tg.add_argument("--token", default=None,
                      help="Bot token (non-interactive mode).")
    p_tg.add_argument("--chat-id", type=int, default=None,
                      help="Chat id (non-interactive mode).")

    args = parser.parse_args(argv)
    if args.cmd != "telegram":
        parser.print_help()
        return 2

    if args.token:
        return _noninteractive_flow(args.token, args.chat_id)
    return _interactive_flow()


if __name__ == "__main__":
    raise SystemExit(main())
