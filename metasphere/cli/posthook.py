"""Stop-hook entry point: ``python -m metasphere.cli.posthook``.

Usage::

    python -m metasphere.cli.posthook              # normal Stop-hook mode
    python -m metasphere.cli.posthook --dry-run    # parse stdin, print
                                                   # JSON plan, no send
    python -m metasphere.cli.posthook --help

Reads the claude-code Stop-hook JSON payload from stdin, runs the
posthook pipeline, and exits 0 unconditionally — the Stop hook must
never break the host.

In ``--dry-run`` mode the payload is parsed as usual but no telegram
send or state-file write occurs; instead a JSON summary is printed to
stdout with ``chat_id``, ``text_length``, ``chunk_count``, and
``would_send``. This is the contract e2e rigs depend on.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from metasphere.paths import resolve
from metasphere.posthook import (
    _resolve_chat_id,
    extract_last_assistant_text,
    read_stop_hook_payload,
    run_posthook,
    should_skip_silent_tick,
)
from metasphere.identity import resolve_agent_id


def _dry_run(stdin_bytes: bytes) -> int:
    paths = resolve()
    payload = read_stop_hook_payload(stdin_bytes)
    agent = resolve_agent_id(paths)

    text: str | None = None
    transcript = payload.get("transcript_path") if isinstance(payload, dict) else None
    if transcript:
        text = extract_last_assistant_text(Path(transcript))

    would_skip = should_skip_silent_tick(text)
    text_str = text or ""
    # Telegram hard-limits messages to 4096 chars; our chunker targets
    # ~4000 to leave room for formatting. Dry-run matches that estimate.
    chunk_size = 4000
    chunk_count = 0 if not text_str else (len(text_str) + chunk_size - 1) // chunk_size
    chat_id = _resolve_chat_id(paths)

    summary = {
        "agent": agent,
        "stop_hook_active": bool(payload.get("stop_hook_active")) if isinstance(payload, dict) else False,
        "chat_id": chat_id,
        "text_length": len(text_str),
        "chunk_count": chunk_count,
        "would_send": (
            not would_skip
            and agent == "@orchestrator"
            and chat_id is not None
            and not (isinstance(payload, dict) and payload.get("stop_hook_active"))
        ),
    }
    print(json.dumps(summary, sort_keys=True))
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] in ("--help", "-h"):
        parser = argparse.ArgumentParser(
            prog="metasphere.cli.posthook",
            description="Claude-code Stop-hook entry point.",
        )
        parser.add_argument("--dry-run", action="store_true",
                            help="parse stdin and print a JSON plan; no sends, no writes")
        parser.print_help()
        return 0
    try:
        stdin_bytes = sys.stdin.buffer.read() if not sys.stdin.isatty() else b""
    except Exception:  # noqa: BLE001
        stdin_bytes = b""
    if "--dry-run" in args:
        try:
            return _dry_run(stdin_bytes)
        except Exception:  # noqa: BLE001 — dry-run must also never break
            return 0
    return run_posthook(stdin_bytes)


if __name__ == "__main__":
    raise SystemExit(main())
