"""Unified ``metasphere`` CLI dispatcher.

Single entry point that lazy-imports per-subcommand handlers via an
import-path registry. Startup stays cheap because nothing under
``metasphere.cli.*`` is imported until the matching subcommand is invoked.

Layout::

    metasphere status
    metasphere ls [path]
    metasphere agent spawn|wake|list|status ...
    metasphere msg send|list|done|reply|all ...
    metasphere task new|start|update|done|list ...
    metasphere telegram send|poll|once|getme ...
    metasphere telegram groups ...
    metasphere hooks posthook|context|git ...
    metasphere schedule ...
    metasphere heartbeat ...
    metasphere memory ...
    metasphere trace ...
    metasphere session ...
    metasphere project ...
    metasphere gateway ...

The legacy ``metasphere-*`` console scripts remain registered as deprecation
shims (see ``metasphere.cli._shims``) and forward into this dispatcher.
"""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from typing import Callable

# subcommand -> "module:function" (lazy import path).
# Two-level groups (telegram groups, hooks git, ...) are resolved by the
# group's own dispatcher; this top-level table only covers the leftmost word.
REGISTRY: dict[str, str] = {
    "agent":     "metasphere.cli.agents:main",
    "msg":       "metasphere.cli.messages:main",
    "task":      "metasphere.cli.tasks:main",
    "telegram":  "metasphere.cli.main:_telegram_dispatch",
    "hooks":     "metasphere.cli.main:_hooks_dispatch",
    "schedule":  "metasphere.cli.schedule:main",
    "heartbeat": "metasphere.cli.heartbeat:main",
    "memory":    "metasphere.cli.memory:main",
    "trace":     "metasphere.cli.trace:main",
    "session":   "metasphere.cli.session:main",
    "sessions":  "metasphere.cli.sessions:main",
    "project":   "metasphere.cli.project:main",
    "gateway":   "metasphere.cli.gateway:main",
    "update":    "metasphere.cli.update:main",
    "consolidate": "metasphere.cli.consolidate:main",
    "status":    "metasphere.cli.main:_legacy_bash",
    "ls":        "metasphere.cli.main:_legacy_bash",
}

_HELP = """\
metasphere - unified CLI for the Metasphere agent system

Usage: metasphere <subcommand> [args...]

Subcommands:
  status                Full system status (legacy bash)
  ls [path]             Project/task/agent landscape (legacy bash)
  agent spawn|wake|list|status ...
  msg send|list|done|reply|all ...
  task new|start|update|done|list ...
  telegram send|poll|once|getme ...
  telegram groups ...
  hooks posthook|context|git ...
  schedule list|run|daemon
  consolidate run [--dry-run] [--since 7d] [--threshold high|medium|low]
  heartbeat tick|daemon
  memory search|index
  trace capture|list|search|prune
  session ...
  sessions all|list|kill-viewer    Multi-agent tmux viewer
  project ...
  gateway status|daemon

Run `metasphere <subcommand> --help` for details.
"""


def _resolve(import_path: str) -> Callable[[list[str]], int]:
    mod_name, func_name = import_path.split(":")
    mod = importlib.import_module(mod_name)
    return getattr(mod, func_name)


def _telegram_dispatch(argv: list[str]) -> int:
    """Route ``metasphere telegram [groups ...]`` to the right module."""
    if argv and argv[0] == "groups":
        return _resolve("metasphere.cli.telegram_groups:main")(argv[1:]) or 0
    return _resolve("metasphere.cli.telegram:main")(argv) or 0


def _hooks_dispatch(argv: list[str]) -> int:
    """Route ``metasphere hooks {posthook|context|git} ...``."""
    if not argv or argv[0] in ("-h", "--help"):
        sys.stdout.write(
            "Usage: metasphere hooks {posthook|context|git} [args...]\n"
        )
        return 0
    head, rest = argv[0], argv[1:]
    table = {
        "posthook": "metasphere.cli.posthook:main",
        "context":  "metasphere.cli.context:main",
        "git":      "metasphere.cli.git_hooks:main",
    }
    if head not in table:
        sys.stderr.write(f"metasphere hooks: unknown subcommand: {head}\n")
        return 2
    return _resolve(table[head])(rest) or 0


def _legacy_bash(argv: list[str]) -> int:
    """Delegate ``metasphere status`` / ``metasphere ls`` to the legacy CLI.

    These subcommands live in ``~/.metasphere/bin/metasphere`` and are
    exec'd with the original args so behaviour is identical.
    """
    # Recover the head subcommand from sys.argv (the dispatcher consumed it).
    head = sys._metasphere_head  # type: ignore[attr-defined]
    bash_path = os.environ.get("METASPHERE_LEGACY_BIN") or os.path.expanduser(
        "~/.metasphere/bin/metasphere"
    )
    if not os.path.isfile(bash_path):
        # Try PATH lookup (avoiding ourselves — only accept a shell script).
        found = shutil.which("metasphere")
        if found and found != sys.argv[0]:
            bash_path = found
        else:
            sys.stderr.write(
                f"metasphere {head}: legacy bash CLI not found at {bash_path}\n"
            )
            return 1
    return subprocess.call([bash_path, head, *argv])


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        sys.stdout.write(_HELP)
        return 0
    head, rest = argv[0], argv[1:]
    target = REGISTRY.get(head)
    if target is None:
        sys.stderr.write(f"metasphere: unknown subcommand: {head}\n\n")
        sys.stderr.write(_HELP)
        return 2
    sys._metasphere_head = head  # type: ignore[attr-defined]
    handler = _resolve(target)
    rc = handler(rest)
    return int(rc or 0)


if __name__ == "__main__":
    sys.exit(main())
