"""Stdout-only emitter for the per-turn context block.

This is the Python parallel of ``scripts/metasphere-context``. It is
NOT yet wired into the precommand hook — the cutover (replacing the
symlink in ``~/.metasphere/bin``) is a separate step.

Usage::

    python -m metasphere.cli.context
"""

from __future__ import annotations

import sys

from metasphere.context import build_context


def main(argv: list[str] | None = None) -> int:
    sys.stdout.write(build_context())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
