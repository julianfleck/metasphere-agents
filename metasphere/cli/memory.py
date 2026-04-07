"""CLI for the memory subpackage.

Usage::

    python -m metasphere.cli.memory search "query" [--limit N] [--strategy fts|cam|hybrid]
    python -m metasphere.cli.memory context "query" [--budget N] [--strategy ...]
    python -m metasphere.cli.memory strategies
"""

from __future__ import annotations

import argparse
import sys

from metasphere.memory import (
    CamStrategy,
    HybridStrategy,
    MemoryStrategy,
    TokenOverlapStrategy,
    context_for,
    recall,
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

    args = p.parse_args(argv)

    if args.cmd == "strategies":
        for name in ("fts", "cam", "hybrid"):
            print(name)
        return 0

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
