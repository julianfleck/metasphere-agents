#!/usr/bin/env python3
"""One-shot migration: quote bare ``@agent`` / ``!label`` frontmatter values.

Historic task files were written with unquoted ``assigned_to: @orchestrator``
and ``priority: !high``. Under a strict YAML loader these parse as tags /
aliases and blow up the render pipeline. After the companion fix in
``metasphere/io.py`` (quote-on-write), new files are safe; this script
normalises the existing backlog.

Behaviour:
  * Reads ``~/.metasphere/projects.json`` for the registered project roots.
  * For each project, walks ``.tasks/active/*.md`` and
    ``.tasks/completed/*.md``.
  * If any value under ``created_by``, ``assigned_to``, or ``priority`` is
    a string starting with ``@`` or ``!`` and is not already quoted on
    disk, rewrites the file via ``write_frontmatter_file`` (which now
    quotes those values).
  * Round-trip safety: parses the new content back and asserts metadata
    matches the original before committing; otherwise skips + reports.

Usage:
  python scripts/migrate_task_frontmatter.py            # do it
  python scripts/migrate_task_frontmatter.py --dry-run  # report only
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make the in-tree metasphere package importable when run from the repo.
_REPO = Path(__file__).resolve().parent.parent
if str(_REPO) not in sys.path:
    sys.path.insert(0, str(_REPO))

from metasphere.io import (  # noqa: E402
    Frontmatter,
    parse_frontmatter,
    serialize_frontmatter,
    write_frontmatter_file,
)

SIGIL_KEYS = ("created_by", "assigned_to", "priority")


def _needs_quoting(fm: Frontmatter) -> bool:
    for k in SIGIL_KEYS:
        v = fm.meta.get(k)
        if isinstance(v, str) and v and v[0] in ("@", "!"):
            return True
    return False


def _already_quoted_on_disk(raw: str) -> bool:
    """Cheap check: if every sigil-value line in the raw text already uses a
    quote, we don't have to rewrite. Safer to still rewrite — serializer
    is idempotent — but this lets --dry-run show a clean "nothing to do"."""
    for line in raw.splitlines():
        s = line.lstrip()
        for k in SIGIL_KEYS:
            if s.startswith(f"{k}:"):
                _, _, v = s.partition(":")
                v = v.strip()
                if v and v[0] in ("@", "!"):
                    return False
    return True


def _iter_task_files(project_path: Path):
    base = project_path / ".tasks"
    for sub in ("active", "completed"):
        d = base / sub
        if not d.is_dir():
            continue
        for p in sorted(d.glob("*.md")):
            yield p


def migrate_file(path: Path, *, dry_run: bool) -> str:
    """Returns one of: 'rewrote', 'skipped-clean', 'no-frontmatter',
    'parse-error', 'roundtrip-mismatch'."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        return f"parse-error:{e}"
    if not raw.lstrip().startswith("---"):
        return "no-frontmatter"
    try:
        fm = parse_frontmatter(raw)
    except Exception as e:
        return f"parse-error:{e}"
    if not fm.meta:
        return "no-frontmatter"
    if not _needs_quoting(fm):
        return "skipped-clean"
    if _already_quoted_on_disk(raw):
        # Parser upgraded the string but disk is actually fine — skip.
        return "skipped-clean"
    # Round-trip safety check before writing.
    new_text = serialize_frontmatter(fm)
    try:
        fm2 = parse_frontmatter(new_text)
    except Exception as e:
        return f"roundtrip-mismatch:parse:{e}"
    if fm2.meta != fm.meta:
        return "roundtrip-mismatch"
    if dry_run:
        return "would-rewrite"
    write_frontmatter_file(path, fm)
    return "rewrote"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--registry",
                    default=str(Path.home() / ".metasphere" / "projects.json"))
    args = ap.parse_args(argv)

    registry_path = Path(args.registry)
    if not registry_path.is_file():
        print(f"registry not found: {registry_path}", file=sys.stderr)
        return 1
    try:
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"registry parse error: {e}", file=sys.stderr)
        return 1

    total_scanned = 0
    total_rewrote = 0
    total_clean = 0
    no_fm_files: list[str] = []
    errors: list[tuple[str, str]] = []
    roundtrip_misses: list[str] = []
    rewrote_list: list[str] = []
    per_project: dict[str, dict[str, int]] = {}

    for entry in registry:
        name = entry.get("name", "?")
        p = Path(entry.get("path", ""))
        if not p.is_dir():
            continue
        counts = {"scanned": 0, "rewrote": 0, "clean": 0, "no_fm": 0, "errors": 0}
        for f in _iter_task_files(p):
            total_scanned += 1
            counts["scanned"] += 1
            result = migrate_file(f, dry_run=args.dry_run)
            if result in ("rewrote", "would-rewrite"):
                total_rewrote += 1
                counts["rewrote"] += 1
                rewrote_list.append(str(f))
            elif result == "skipped-clean":
                total_clean += 1
                counts["clean"] += 1
            elif result == "no-frontmatter":
                no_fm_files.append(str(f))
                counts["no_fm"] += 1
            elif result.startswith("parse-error"):
                errors.append((str(f), result))
                counts["errors"] += 1
            elif result.startswith("roundtrip-mismatch"):
                roundtrip_misses.append(str(f))
                counts["errors"] += 1
        if counts["scanned"]:
            per_project[name] = counts

    verb = "would rewrite" if args.dry_run else "rewrote"
    print(f"Scanned {total_scanned} files across {len(per_project)} projects.")
    print(f"  {verb}: {total_rewrote}")
    print(f"  clean  : {total_clean}")
    print(f"  no-fm  : {len(no_fm_files)}")
    print(f"  errors : {len(errors)}")
    print(f"  rt-miss: {len(roundtrip_misses)}")
    print()
    print("Per project:")
    for name, c in sorted(per_project.items()):
        print(f"  {name}: scanned={c['scanned']} rewrote={c['rewrote']} "
              f"clean={c['clean']} no_fm={c['no_fm']} errors={c['errors']}")
    if rewrote_list:
        print(f"\nFiles {verb}:")
        for f in rewrote_list:
            print(f"  {f}")
    if no_fm_files:
        print(f"\nFiles without frontmatter ({len(no_fm_files)}):")
        for f in no_fm_files:
            print(f"  {f}")
    if errors:
        print(f"\nParse errors ({len(errors)}):")
        for f, msg in errors:
            print(f"  {f}: {msg}")
    if roundtrip_misses:
        print(f"\nRound-trip mismatches ({len(roundtrip_misses)}) — NOT rewritten:")
        for f in roundtrip_misses:
            print(f"  {f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
