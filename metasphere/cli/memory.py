"""``metasphere memory`` — query the project-memory store.

Search front-end for ``metasphere.memory``. Routes search queries
through the hybrid (FTS + structured) backend and renders hits either
as a free-form listing or as a ready-to-paste context block for an
agent prompt. The shim itself is stateless; ingestion + indexing happen
inside the memory subpackage and during posthook capture, not here.
"""

from __future__ import annotations

DESCRIPTION = "Search project memory + format memory hits as a context block."

USAGE = """\
Usage: metasphere memory <command> [args...]

Commands:
  search "query" [--limit N] [--strategy fts|cam|hybrid]
                                Search memory and print top-N hits.
  context "query" [--budget N] [--strategy ...]
                                Format memory hits as a context block
                                ready for prompt injection.
  strategies                    List available retrieval strategies.
  lint [--max-line N] [--root PATH] [--suggest] [--apply]
                                Check MEMORY.md for overlong index lines,
                                dead [title](file.md) links, orphan topic
                                files (on disk, no index entry), and topic
                                files whose frontmatter description is itself
                                overlong (the root cause of index lines that
                                --apply can't shrink).
                                Exits 1 on any violation (report-only).

Strategies:
  fts                           Token overlap (default fallback).
  cam                           Collective Agent Memory recall.
  hybrid                        Both, scored together.
"""


import argparse
import sys
from pathlib import Path

from metasphere.memory import (
    CamStrategy,
    HybridStrategy,
    MemoryStrategy,
    TokenOverlapStrategy,
    context_for,
    recall,
)
from metasphere.memory.lint import (
    DEFAULT_MAX_LINE,
    apply_suggestions,
    lint_index,
    render as render_lint,
    render_apply,
)


def _strategy(name: str | None) -> list[MemoryStrategy] | None:
    if not name:
        return None
    if name == "fts":
        return [TokenOverlapStrategy()]
    if name == "cam":
        return [CamStrategy()]
    if name == "hybrid":
        return [HybridStrategy([CamStrategy(), TokenOverlapStrategy()])]
    raise SystemExit(f"unknown strategy: {name}")


def main(argv: list[str] | None = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list and args_list[0] in ("--help", "-h"):
        sys.stdout.write(USAGE)
        return 0
    p = argparse.ArgumentParser(prog="metasphere memory")
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("search", help="search memory and print top-N hits")
    s.add_argument("query")
    s.add_argument("--limit", type=int, default=5)
    s.add_argument("--strategy", choices=("fts", "cam", "hybrid"), default=None)

    c = sub.add_parser("context", help="format memory hits as a context block")
    c.add_argument("query")
    c.add_argument("--budget", type=int, default=2048)
    c.add_argument("--strategy", choices=("fts", "cam", "hybrid"), default=None)

    sub.add_parser("strategies", help="list available strategies")

    ln = sub.add_parser("lint", help="check MEMORY.md index hygiene")
    ln.add_argument("--max-line", type=int, default=DEFAULT_MAX_LINE)
    ln.add_argument("--root", type=Path, default=None)
    ln.add_argument(
        "--suggest",
        action="store_true",
        help="for each overlong line, propose a shorter entry built from "
        "the linked topic file's frontmatter description",
    )
    ln.add_argument(
        "--apply",
        action="store_true",
        help="rewrite overlong lines in place with their suggestions "
        "(only strict-shorter, non-lossy swaps; entry count preserved)",
    )

    args = p.parse_args(args_list)

    if args.cmd == "strategies":
        for name in ("fts", "cam", "hybrid"):
            print(name)
        return 0

    if args.cmd == "lint":
        if args.apply:
            result = apply_suggestions(args.root, max_line=args.max_line)
            print(render_apply(result))
            return 0
        report = lint_index(args.root, max_line=args.max_line, suggest=args.suggest)
        print(render_lint(report))
        return 0 if report.ok else 1

    strategies = _strategy(args.strategy)

    if args.cmd == "search":
        hits = recall(args.query, limit=args.limit, strategies=strategies)
        if not hits:
            print("(no hits)")
            return 0
        for h in hits:
            print(f"{h.score:.3f}  {h.source}")
            if h.excerpt:
                print(f"    {h.excerpt}")
        return 0

    if args.cmd == "context":
        sys.stdout.write(context_for(args.query, budget_chars=args.budget, strategies=strategies))
        return 0

    return 1


if __name__ == "__main__":
    raise SystemExit(main())
