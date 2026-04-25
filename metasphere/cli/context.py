"""Stdout-only emitter for the per-turn context block.

Wired in claude-code as the ``UserPromptSubmit`` hook command:

    python3 -m metasphere.cli.context

In addition to printing the context block to stdout, this entry point
writes a per-turn success **breadcrumb** so the Stop posthook can
*fail closed* when the context build crashed. See
``metasphere.breadcrumbs`` for the protocol — without a matching
success breadcrumb the posthook MUST suppress the auto-forward of the
assistant's reply to Telegram, since the reply was generated against
an incomplete context block.

Reads ``session_id`` and ``transcript_path`` from the claude-code hook
payload on stdin. If stdin is not a JSON payload (e.g. the binary is
invoked directly from a shell), no breadcrumb is written — the
posthook treats absence as failure too, which is the correct
fail-closed default.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from metasphere import breadcrumbs as _bc
from metasphere.context import build_context
from metasphere.identity import resolve_agent_id
from metasphere.paths import resolve


def _parse_payload(stdin_bytes: bytes) -> dict:
    if not stdin_bytes:
        return {}
    try:
        obj = json.loads(stdin_bytes.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def main(argv: list[str] | None = None) -> int:
    # Read stdin defensively — manual invocation from a shell has no
    # JSON payload, in which case the breadcrumb write is skipped and
    # the posthook will fail-closed for that session (correct).
    try:
        stdin_bytes = sys.stdin.buffer.read() if not sys.stdin.isatty() else b""
    except Exception:  # noqa: BLE001
        stdin_bytes = b""

    payload = _parse_payload(stdin_bytes)
    session_id = str(payload.get("session_id") or "")
    transcript_path = payload.get("transcript_path") or ""

    paths = resolve()
    agent = resolve_agent_id(paths)
    user_msg_count = _bc.count_user_messages(transcript_path) if transcript_path else 0

    # UserPromptSubmit is one of the four hook signals reap_dormant
    # uses to decide an agent is alive. Touch BEFORE the context build
    # so even if the build crashes the supervisor still sees input
    # arrived. Best-effort by contract — touch_last_active swallows.
    from metasphere.agents import touch_last_active
    touch_last_active(agent, paths)

    try:
        block = build_context()
        sys.stdout.write(block)
    except Exception as exc:  # noqa: BLE001 — context build must not crash the host
        # Write the FAILED breadcrumb so the posthook fail-closes this
        # turn. We deliberately do NOT re-raise: the UserPromptSubmit
        # hook is best-effort, and crashing it would break the user's
        # ability to interact with the agent at all.
        if session_id:
            _bc.write_breadcrumb(
                paths,
                session_id=session_id,
                status=_bc.STATUS_FAILED,
                user_msg_count=user_msg_count,
                agent=agent,
                reason=f"{type(exc).__name__}: {exc}"[:200],
            )
        # Emit a minimal context fragment so the agent at least gets
        # *something*; the failed breadcrumb ensures the posthook
        # suppresses the resulting reply from Telegram.
        try:
            sys.stdout.write(
                "## Metasphere context build failed\n"
                f"_({type(exc).__name__})_\n"
            )
        except Exception:  # noqa: BLE001
            pass
        return 0

    # Happy path: stamp success and opportunistically prune old entries.
    if session_id:
        _bc.write_breadcrumb(
            paths,
            session_id=session_id,
            status=_bc.STATUS_SUCCESS,
            user_msg_count=user_msg_count,
            agent=agent,
        )
        try:
            _bc.prune_old_breadcrumbs(paths)
        except Exception:  # noqa: BLE001
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
