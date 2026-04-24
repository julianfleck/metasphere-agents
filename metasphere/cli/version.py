"""``metasphere version`` — print version + HEAD commit hash."""

from __future__ import annotations

import subprocess
import sys
import tomllib
from pathlib import Path


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


def _read_pyproject_version() -> str | None:
    """Read [project].version from pyproject.toml at the package source root.

    For an editable install this is the live source-of-truth that bump-minor.yml
    edits. Returns None if pyproject.toml is not present (non-editable wheel)
    so the caller can fall back to pip metadata.
    """
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    if not pyproject.is_file():
        return None
    try:
        with pyproject.open("rb") as f:
            data = tomllib.load(f)
        v = data.get("project", {}).get("version")
        return v if isinstance(v, str) else None
    except Exception:
        return None


def _resolve_version() -> str:
    """Source-of-truth first: live pyproject.toml, then pip dist-info, then 0.0.0.

    Editable installs freeze pip's dist-info at ``pip install -e .`` time and
    never refresh it, so ``importlib.metadata.version`` reports a stale value
    between bumps. Reading pyproject.toml directly gives the real version.
    """
    v = _read_pyproject_version()
    if v:
        return v
    try:
        from importlib.metadata import version as _pkg_version

        return _pkg_version("metasphere")
    except Exception:
        return "0.0.0"


def main(argv: list[str] | None = None) -> int:
    print(f"metasphere {_resolve_version()}")
    print(f"commit: {_head_hash()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
