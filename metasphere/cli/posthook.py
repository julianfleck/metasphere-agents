"""Stop-hook entry point: ``python -m metasphere.cli.posthook``.

Reads the claude-code Stop-hook JSON payload from stdin, runs the
posthook pipeline, and exits 0 unconditionally — the Stop hook must
never break the host.
"""

from __future__ import annotations

import sys

from metasphere.posthook import run_posthook


def main(argv: list[str] | None = None) -> int:
    try:
        stdin_bytes = sys.stdin.buffer.read() if not sys.stdin.isatty() else b""
    except Exception:  # noqa: BLE001
        stdin_bytes = b""
    return run_posthook(stdin_bytes)


if __name__ == "__main__":
    raise SystemExit(main())
