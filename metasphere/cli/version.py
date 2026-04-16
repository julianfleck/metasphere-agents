"""``metasphere version`` — print version + HEAD commit hash."""

from __future__ import annotations

import subprocess
import sys
from importlib.metadata import version as _pkg_version


def _head_hash() -> str:
    """Return the first 12 chars of HEAD, or ``(unknown)``."""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()[:12]
    except Exception:
        pass
    return "(unknown)"


def main(argv: list[str] | None = None) -> int:
    try:
        ver = _pkg_version("metasphere")
    except Exception:
        ver = "0.0.0"
    print(f"metasphere {ver}")
    print(f"commit: {_head_hash()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
