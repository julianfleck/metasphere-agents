"""CLI: ``python -m metasphere.cli.project``.

    project init [path]
    project list
    project changelog [name]
    project learnings [name]
"""

from __future__ import annotations

import sys
from pathlib import Path

from metasphere.paths import resolve
from metasphere.project import (
    init_project,
    list_projects,
    project_changelog,
    project_learnings,
)


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args:
        print(__doc__, file=sys.stderr)
        return 2
    cmd, *rest = args
    paths = resolve()

    if cmd in ("init", "new"):
        target = Path(rest[0]) if rest else Path.cwd()
        p = init_project(path=target, paths=paths)
        print(f"Initialized project: {p.name}")
        print(f"  Path: {p.path}")
        return 0

    if cmd in ("list", "ls"):
        rows = list_projects(paths=paths)
        if not rows:
            print("(no projects)")
            return 0
        for p in rows:
            print(f"{p.name}\t{p.status}\t{p.path}")
        return 0

    if cmd in ("changelog", "changes"):
        try:
            f = project_changelog(rest[0] if rest else None, paths=paths)
        except FileNotFoundError as e:
            print(str(e), file=sys.stderr)
            return 1
        print(f"wrote {f}")
        return 0

    if cmd in ("learnings", "learn"):
        try:
            f = project_learnings(rest[0] if rest else None, paths=paths)
        except FileNotFoundError as e:
            print(str(e), file=sys.stderr)
            return 1
        print(f"wrote {f}")
        return 0

    print(f"unknown subcommand: {cmd}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
