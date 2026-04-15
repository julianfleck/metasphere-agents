"""``metasphere migrate-project-dirs`` — move project-scoped content to the
canonical ``~/.metasphere/projects/<name>/`` layout.

Per Julian 2026-04-14: the single canonical location for everything
project-scoped is ``~/.metasphere/projects/<name>/``. In practice, two
legacy patterns exist on disk:

1. Tasks live at ``<registered_repo>/.tasks/`` instead of
   ``~/.metasphere/projects/<name>/.tasks/``.
2. ``project.json`` lives at ``<registered_repo>/.metasphere/project.json``
   instead of ``~/.metasphere/projects/<name>/project.json`` (only the
   worldwire project hits this, AFAIK).

PR #8 scope: migrate **tasks only** (`--what tasks`, the default).
Messages, changelog, and learnings land in a follow-up PR that reuses
the same ``--what {all,messages,changelog,learnings}`` flag surface.

The migration is idempotent and refuses on conflict:

- If the dest ``.tasks/`` doesn't exist yet → move the whole tree.
- If the dest exists and is empty → remove dest, move tree.
- If both src and dest have content → skip with a clear error line
  and non-zero exit (operator must resolve manually).
- ``--dry-run`` prints the plan without touching disk.

Usage::

    metasphere migrate-project-dirs                 # dry-run tasks for every registered project
    metasphere migrate-project-dirs --apply
    metasphere migrate-project-dirs --project foo --apply
    metasphere migrate-project-dirs --what tasks --apply
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Iterable, List

from metasphere import project as _project
from metasphere.paths import Paths, resolve


WHAT_CHOICES = ("tasks", "messages", "changelog", "learnings", "all")
#: What each ``--what`` token is named in the source (repo) layout and
#: the destination (``~/.metasphere/projects/<name>/``) layout. Same
#: relative name in every case — the whole point of the canonical
#: layout is name-preservation.
_DIRNAME = {
    "tasks": ".tasks",
    "messages": ".messages",
    "changelog": ".changelog",
    "learnings": ".learnings",
}


def _iter_targets(what: str) -> Iterable[str]:
    if what == "all":
        return ("tasks", "messages", "changelog", "learnings")
    return (what,)


def _plan_move(src: Path, dst: Path) -> tuple[str, str]:
    """Return ``(action, reason)`` for a prospective src→dst move.

    Actions:
      - ``skip``  — src doesn't exist or is empty (nothing to migrate)
      - ``move``  — safe to move (dst missing, or dst empty)
      - ``conflict`` — both src and dst have content; manual resolution
    """
    if not src.exists() or not src.is_dir():
        return ("skip", "src missing")
    src_has_content = any(src.iterdir())
    if not src_has_content:
        return ("skip", "src empty")
    if not dst.exists():
        return ("move", "dst missing → whole-tree move")
    if not any(dst.iterdir()):
        return ("move", "dst empty → replace")
    return ("conflict", "both src and dst have content")


def _merge_tree(src: Path, dst: Path) -> None:
    """Move src→dst, merging directories. Preserves dst files when src lacks
    them (we only ever run this when _plan_move says ``move``, i.e. dst
    empty or missing — so merging is a no-op safety net).
    """
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        shutil.move(str(src), str(dst))
        return
    # dst exists and is empty per our caller: remove + move.
    dst.rmdir()
    shutil.move(str(src), str(dst))


def _run_migration(
    paths: Paths,
    *,
    only_project: str | None,
    what: str,
    apply: bool,
) -> int:
    """Execute (or dry-run) the migration. Returns a process exit code:
    0 on clean run, 2 on any unresolvable conflict.
    """
    registry = _project._load_registry(paths)
    if only_project:
        registry = [e for e in registry if e.get("name") == only_project]
        if not registry:
            print(f"no registered project named {only_project!r}", file=sys.stderr)
            return 2

    targets = _iter_targets(what)
    had_conflict = False
    any_moved = False

    for entry in registry:
        name = entry.get("name", "")
        repo_path = Path(entry.get("path", "")).expanduser()
        if not name:
            continue
        project_dir = paths.projects / name
        print(f"\n[{name}]")
        print(f"  repo: {repo_path}")
        print(f"  dest: {project_dir}")

        for tgt in targets:
            dirname = _DIRNAME[tgt]
            src = repo_path / dirname
            dst = project_dir / dirname
            action, reason = _plan_move(src, dst)
            tag = "DRY-RUN" if not apply else "APPLY"
            print(f"  {tag} {tgt}: {action} ({reason})")
            print(f"    src: {src}")
            print(f"    dst: {dst}")
            if action == "conflict":
                had_conflict = True
            if action == "move" and apply:
                _merge_tree(src, dst)
                any_moved = True

    print()
    if had_conflict:
        print("migration finished with CONFLICTS — resolve src vs dst manually, "
              "then re-run to pick up the remaining items", file=sys.stderr)
        return 2
    if apply and any_moved:
        print("migration complete.", file=sys.stdout)
    elif not apply:
        print("(dry-run — pass --apply to commit)", file=sys.stdout)
    return 0


def main(argv: List[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="metasphere migrate-project-dirs",
        description=(
            "Move per-project dirs (tasks / messages / changelog / learnings) "
            "from <repo>/.tasks/ etc. into ~/.metasphere/projects/<name>/."
        ),
    )
    parser.add_argument(
        "--project", default=None,
        help="only migrate the named registered project",
    )
    parser.add_argument(
        "--what", choices=WHAT_CHOICES, default="tasks",
        help="which per-project dir to migrate (default: tasks)",
    )
    parser.add_argument(
        "--apply", action="store_true",
        help="actually perform the moves (default: dry-run)",
    )
    args = parser.parse_args(argv)

    paths = resolve()
    return _run_migration(
        paths,
        only_project=args.project,
        what=args.what,
        apply=args.apply,
    )


if __name__ == "__main__":
    raise SystemExit(main())
