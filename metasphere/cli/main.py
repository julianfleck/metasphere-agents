"""Unified ``metasphere`` CLI dispatcher.

Single entry point that lazy-imports per-subcommand handlers via the
import-path registry in :mod:`metasphere.cli._registry`. Startup stays
cheap because nothing under ``metasphere.cli.*`` is imported until the
matching subcommand is invoked.

Top-level help (``metasphere --help``) and ``docs/CLI.md`` are both
generated from the same source: each handler module's
``DESCRIPTION`` and ``USAGE`` constants. To add a new subcommand,
register it in ``_registry.SUBCOMMANDS`` and define the two constants
+ a ``main()`` in the new handler module — nothing in this file needs
to change.
"""

from __future__ import annotations

import sys

from metasphere.cli import _registry


# Re-export so existing callers (and tests) that import REGISTRY from
# ``metasphere.cli.main`` keep working.
REGISTRY = _registry.REGISTRY


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        sys.stdout.write(_registry.render_top_help())
        return 0
    head, rest = argv[0], argv[1:]
    if head not in _registry.REGISTRY:
        sys.stderr.write(f"metasphere: unknown subcommand: {head}\n\n")
        sys.stderr.write(_registry.render_top_help())
        return 2
    handler = _registry.resolve(head)
    rc = handler(rest)
    return int(rc or 0)


if __name__ == "__main__":
    sys.exit(main())
