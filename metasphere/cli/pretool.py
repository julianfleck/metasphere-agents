"""PreToolUse hook entry point — guard interactive prompts in gateway sessions.

Gateway-spawned agents (``@orchestrator`` and every project agent) run in a
tmux pane with no interactive human at the keyboard. Their only input path is
Telegram → injected paste → ``C-m``, which delivers prompt *text* — it cannot
drive an interactive TUI widget (arrow-key menus, multi-select toggles). When
Claude Code renders ``AskUserQuestion`` or ``ExitPlanMode`` in such a session,
the widget can never receive input and the session hangs until a human
``tmux attach``es.

This hook fires *before* those tools execute. In a gateway session (marked by
``METASPHERE_GATEWAY_SESSION=1``, exported by the respawn loop in
``metasphere/gateway/session.py``) it DENIES the call and returns a reason that
redirects the agent to its native async idiom — Telegram / message inbox
round-trip — so the widget never renders and the hang state never exists.

Discipline (same as the posthook): the hook must NEVER break the host. Any
exception, malformed stdin, or unexpected state falls through to ALLOW
(exit 0, no output). A human running ``claude`` by hand never has the env var
set, so the hook is inert for interactive use.
"""

from __future__ import annotations

import json
import os
import sys

from metasphere.identity import resolve_agent_id
from metasphere.paths import resolve


DESCRIPTION = "PreToolUse hook: block interactive prompts in gateway sessions."

USAGE = """\
Usage: metasphere hooks pretool

PreToolUse-hook entrypoint. Wired into Claude Code via
~/.metasphere/.claude/settings.local.json (matcher
'AskUserQuestion|ExitPlanMode'). Reads the PreToolUse JSON payload from
stdin. In a gateway session (METASPHERE_GATEWAY_SESSION=1) it denies
AskUserQuestion / ExitPlanMode and returns a redirect reason telling the
agent to ask via the async Telegram/inbox round-trip instead. Otherwise
it allows the call (no output). Default-allow on any error — the hook
must never break the host turn. Always exits 0.
"""

# Tool calls that render an interactive TUI widget needing keyboard input.
# A gateway session has no keyboard, so these hang it. The install.sh
# matcher pre-filters to these names; this set re-checks so correctness
# never depends on the matcher being exhaustive.
INTERACTIVE_TOOLS = ("AskUserQuestion", "ExitPlanMode")


def _redirect_reason(tool: str) -> str:
    """Build the deny reason — the load-bearing UX surface.

    Branches on agent identity: ``@orchestrator`` owes the question to
    the operator (Telegram); any other agent owes it to its lead/parent via the
    message system, NOT the operator's Telegram. Identity + parent are resolved
    best-effort; on any failure we degrade to the orchestrator phrasing.
    """
    try:
        paths = resolve()
        agent = resolve_agent_id(paths)
    except Exception:  # noqa: BLE001 — never let resolution break the deny
        agent = "@orchestrator"

    if agent == "@orchestrator":
        ask = (
            'To ask the operator, run: metasphere telegram send "<your question, '
            'with numbered options>" then await their answer in your message '
            "inbox (metasphere msg), which you read at the start of every turn."
        )
    else:
        parent = "@orchestrator"
        try:
            agent_dir = paths.find_agent_dir(agent)
            if agent_dir is not None:
                p = (agent_dir / "parent").read_text(encoding="utf-8").strip()
                if p:
                    parent = p
        except Exception:  # noqa: BLE001 — fall back to @orchestrator
            pass
        ask = (
            f'To ask, run: metasphere msg send {parent} "<your question, with '
            'numbered options>" then await the reply in your message inbox '
            "(metasphere msg), which you read at the start of every turn. "
            "Do NOT message the operator's Telegram directly."
        )

    if tool == "ExitPlanMode":
        return (
            "ExitPlanMode is disabled in gateway sessions — you have no "
            "interactive terminal to approve a plan, so this would hang your "
            "session. Permissions are already skipped here, so you may simply "
            "proceed with the work. If the plan genuinely needs sign-off: "
            + ask
        )
    return (
        "AskUserQuestion is disabled in gateway sessions — you have no "
        "interactive terminal here, so this prompt cannot receive input and "
        "would hang your session. " + ask
    )


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] in ("--help", "-h"):
        sys.stdout.write(USAGE)
        return 0

    # Default-allow safety: wrap the entire body so ANY failure (bad stdin,
    # JSON error, missing env, resolution error) falls through to allow.
    try:
        if not os.environ.get("METASPHERE_GATEWAY_SESSION"):
            return 0  # interactive human (or non-gateway) — allow

        try:
            stdin_bytes = sys.stdin.buffer.read() if not sys.stdin.isatty() else b""
        except Exception:  # noqa: BLE001
            stdin_bytes = b""
        payload = json.loads(stdin_bytes.decode("utf-8")) if stdin_bytes else {}
        tool = payload.get("tool_name", "") if isinstance(payload, dict) else ""

        if tool not in INTERACTIVE_TOOLS:
            return 0  # not an interactive tool — allow

        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": _redirect_reason(tool),
            }
        }))
        return 0
    except Exception:  # noqa: BLE001 — the hook must never break the host
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
