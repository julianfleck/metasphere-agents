"""``metasphere hooks {posthook|context|git}`` — Claude Code hook entrypoints.

Routes to the per-event hook handlers installed into
``~/.metasphere/.claude/settings.local.json`` by ``install.sh``.
"""

from __future__ import annotations

import importlib
import sys


DESCRIPTION = "Per-turn Claude Code hook entrypoints (posthook|context|pretool|git)."

USAGE = """\
Usage: metasphere hooks {posthook|context|pretool|git} [args...]

Sub-events:
  posthook   Stop-hook: route the assistant's last turn to Telegram and
             enforce dormancy/idle bookkeeping.
  context    UserPromptSubmit hook: inject the per-turn context block
             (messages, tasks, voice/mission, project context, CAM hits).
  pretool    PreToolUse hook: deny interactive prompts (AskUserQuestion,
             ExitPlanMode) in gateway sessions and redirect to the async
             Telegram/inbox round-trip. Inert outside gateway sessions.
  git        Pre-commit / pre-push helpers (identifier-leak guard +
             lint shim). Used by the per-agent git hook installer.

Each sub-event has its own --help.
"""


_TABLE = {
    "posthook": "metasphere.cli.posthook:main",
    "context":  "metasphere.cli.context:main",
    "pretool":  "metasphere.cli.pretool:main",
    "git":      "metasphere.cli.git_hooks:main",
}


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        sys.stdout.write(USAGE)
        return 0
    head, rest = argv[0], argv[1:]
    target = _TABLE.get(head)
    if target is None:
        sys.stderr.write(f"metasphere hooks: unknown sub-event: {head}\n\n")
        sys.stderr.write(USAGE)
        return 2
    mod_name, func_name = target.split(":")
    mod = importlib.import_module(mod_name)
    return getattr(mod, func_name)(rest) or 0


if __name__ == "__main__":
    raise SystemExit(main())
