"""Deprecation shims for the legacy ``metasphere-*`` console scripts.

Each shim:

  1. Emits a one-line deprecation notice on stderr — but ONLY when stderr is a
     TTY, so systemd units, hook callers and pipelines don't get spammed.
  2. Forwards ``sys.argv[1:]`` to :func:`metasphere.cli.main.main` with the
     new subcommand prefix prepended.

The aim is zero behaviour change for callers; once every caller has migrated
the shims (and their pyproject entries) can be deleted.
"""

from __future__ import annotations

import sys
from typing import Sequence


def _warn(old: str, new: str) -> None:
    if sys.stderr.isatty():
        sys.stderr.write(
            f"warning: `{old}` is deprecated; use `metasphere {new}` instead\n"
        )


def _forward(prefix: Sequence[str]) -> int:
    from metasphere.cli.main import main as _main
    return _main([*prefix, *sys.argv[1:]])


def _make(old: str, prefix: Sequence[str]):
    def _shim() -> int:
        _warn(old, " ".join(prefix))
        return _forward(prefix)
    _shim.__name__ = f"shim_{old.replace('-', '_')}"
    _shim.__doc__ = f"Deprecation shim: `{old}` -> `metasphere {' '.join(prefix)}`"
    return _shim


# Console-script entrypoints (referenced from pyproject.toml).
messages_shim              = _make("messages", ["msg"])
tasks_shim                 = _make("tasks", ["task"])
metasphere_spawn           = _make("metasphere-spawn", ["agent", "spawn"])
metasphere_wake            = _make("metasphere-wake", ["agent", "wake"])
metasphere_agent           = _make("metasphere-agent", ["agent"])
metasphere_context         = _make("metasphere-context", ["hooks", "context"])
metasphere_posthook        = _make("metasphere-posthook", ["hooks", "posthook"])
metasphere_git_hooks       = _make("metasphere-git-hooks", ["hooks", "git"])
metasphere_telegram        = _make("metasphere-telegram", ["telegram"])
metasphere_telegram_groups = _make("metasphere-telegram-groups", ["telegram", "groups"])
metasphere_schedule        = _make("metasphere-schedule", ["schedule"])
metasphere_heartbeat       = _make("metasphere-heartbeat", ["heartbeat"])
metasphere_fts             = _make("metasphere-fts", ["memory"])
metasphere_trace           = _make("metasphere-trace", ["trace"])
metasphere_session         = _make("metasphere-session", ["session"])
metasphere_project         = _make("metasphere-project", ["project"])
metasphere_gateway         = _make("metasphere-gateway", ["gateway"])
