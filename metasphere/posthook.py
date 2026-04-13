"""Post-turn (Stop) hook: route assistant reply to Telegram + track activity.

Two responsibilities:

1. **Route** the final assistant text of a turn to Telegram for the
   ``@orchestrator`` agent only. Sub-agents communicate via the messages
   system, never Telegram.
2. **Track** turn completion: bump per-agent activity counter, update
   ``updated_at``, and log a heartbeat event every 10 turns.

The hook **must never raise**. Any exception is logged and swallowed; the
top-level entry point always returns ``0`` so claude-code's Stop pipeline
keeps working even if metasphere is broken.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import traceback
from pathlib import Path
from typing import Any

from .events import log_event
from .identity import resolve_agent_id
from .io import atomic_write_text, file_lock, read_json, write_json
from .paths import Paths, resolve


# ---------- payload + transcript parsing ----------

def read_stop_hook_payload(stdin_bytes: bytes) -> dict:
    """Parse the JSON Stop-hook payload from claude-code.

    Empty / invalid input returns ``{}`` rather than raising — the hook
    is occasionally invoked manually with no stdin.
    """
    if not stdin_bytes:
        return {}
    try:
        return json.loads(stdin_bytes.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return {}


def extract_last_assistant_text(transcript_path: Path) -> str | None:
    """Walk a JSONL transcript backwards and return the most recent
    assistant message's concatenated text content.

    Returns ``None`` if the file is missing, empty, or contains no
    assistant message with text blocks.
    """
    p = Path(transcript_path)
    if not p.exists():
        return None
    try:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") != "assistant":
            continue
        msg = obj.get("message") or {}
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                t = block.get("text") or ""
                if t:
                    parts.append(t)
        if parts:
            return "\n".join(parts)
        # No text blocks on the last assistant turn — turn ended on a
        # tool call. Treat as silent and stop walking.
        return None
    return None


# ---------- silent-tick filter ----------

def should_skip_silent_tick(text: str | None) -> bool:
    """Return True if the turn produced no user-facing text and should
    not be routed to Telegram. Per CLAUDE.md "Heartbeat Turn Etiquette",
    silent ticks must be silent — no placeholders.
    """
    if text is None:
        return True
    stripped = text.strip()
    if not stripped:
        return True
    # Match the standardized idle token and common variants
    if _IDLE_PATTERN.match(stripped):
        return True
    return False


# ---------- telegram routing ----------

def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _last_sent_path(paths: Paths) -> Path:
    return paths.state / "posthook_last_sent"


def _telegram_error_log(paths: Paths) -> Path:
    return paths.state / "posthook_telegram_errors.log"


def _explicit_send_marker_path(paths: Paths) -> Path:
    """Marker touched by ``metasphere-telegram send`` when the sender is
    @orchestrator. Posthook reads its mtime to decide whether to suppress
    the auto-forward of the final assistant text.
    """
    return paths.state / "orchestrator_explicit_send_at"


# Window inside which an explicit @orchestrator send suppresses the
# Stop-hook auto-forward. Long enough to span the gap between the CLI
# call and the assistant text being finalized; short enough that a stale
# marker from an earlier turn does not silence a genuinely new message.
EXPLICIT_SEND_SUPPRESS_WINDOW_SECONDS = 120


def _explicit_send_marker_fresh(paths: Paths) -> bool:
    marker = _explicit_send_marker_path(paths)
    try:
        if not marker.exists():
            return False
        age = _dt.datetime.now(_dt.timezone.utc).timestamp() - marker.stat().st_mtime
        return 0 <= age <= EXPLICIT_SEND_SUPPRESS_WINDOW_SECONDS
    except OSError:
        return False


def mark_orchestrator_explicit_send(paths: Paths) -> None:
    """Touch the explicit-send marker. Called by metasphere-telegram CLI
    after a successful @orchestrator send so the next Stop-hook tick
    knows to suppress the duplicate auto-forward.
    """
    marker = _explicit_send_marker_path(paths)
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.touch()
    except OSError:
        pass


import re as _re

# The standardized idle token agents emit on silent heartbeat ticks.
# CLAUDE.md instructs agents to emit exactly "[idle]" when there's
# nothing to report. The posthook filters it out so it never reaches
# Telegram. Also matches common free-form variants as a safety net.
_IDLE_TOKEN = "[idle]"
_IDLE_PATTERN = _re.compile(
    r"^\s*(?:\[idle\]|standing by|silent tick|quiet|nothing to report|still here)\.?\s*$",
    _re.IGNORECASE,
)


def route_to_telegram(text: str, paths: Paths) -> None:
    """Send ``text`` to Telegram, deduping
    against the last-sent hash. Failures are logged, never raised.
    """
    if not text:
        return

    # Filter idle-tick placeholders — never forward these.
    if _IDLE_PATTERN.match(text.strip()):
        return

    digest = _hash_text(text)
    last_file = _last_sent_path(paths)
    try:
        if last_file.exists():
            prev = last_file.read_text(encoding="utf-8").strip()
            if prev == digest:
                return
    except OSError:
        pass

    try:
        from .telegram import api as telegram_api

        chat_id = _resolve_chat_id(paths)
        if chat_id is None:
            _log_telegram_error(paths, "no chat_id configured (telegram_chat_id missing)")
            return
        telegram_api.send_message(chat_id, text)
    except Exception as exc:  # noqa: BLE001 — must never raise
        # Persist nothing on failure: a transient send error must not
        # poison the dedupe state and silently swallow the next retry.
        _log_telegram_error(paths, f"{type(exc).__name__}: {exc}")
        return

    # Only persist the dedupe hash after a confirmed-good send.
    try:
        atomic_write_text(last_file, digest + "\n")
    except OSError:
        pass


def consume_pending_ack(paths: Paths) -> None:
    """Replace the gateway's 👀 with 👍 on the user message that triggered
    this turn. Called from ``run_posthook`` after we know the turn produced
    user-visible text — regardless of which path (auto-forward or explicit
    ``metasphere-telegram send``) delivered the reply. Always consumes the
    marker so a stale entry can't ack the next unrelated turn. Best-effort.
    """
    pending = paths.state / "telegram_pending_ack.json"
    try:
        if not pending.exists():
            return
        data = json.loads(pending.read_text(encoding="utf-8"))
        try:
            pending.unlink()
        except OSError:
            pass
        cid = data.get("chat_id")
        mid = data.get("message_id")
        if cid is None or mid is None:
            return
        try:
            from .telegram import api as telegram_api
            telegram_api.set_message_reaction(cid, int(mid), "👍")
        except Exception as exc:  # noqa: BLE001
            _log_telegram_error(paths, f"ack reaction failed: {exc}")
    except Exception as exc:  # noqa: BLE001
        _log_telegram_error(paths, f"ack pending read failed: {exc}")


def _resolve_chat_id(paths: Paths) -> str | None:
    """Read the Telegram chat id from the standard config locations.

    Resolution order matches the install-script defaults:

    1. ``$paths.config/telegram.env`` parsed for ``TELEGRAM_CHAT_ID=...``
       (the canonical openclaw/install-script layout — KEY=VALUE env file).
    2. ``$paths.config/telegram_chat_id`` (one-line bare value).
    3. ``$paths.root/telegram_chat_id`` (legacy fallback).

    Loads the chat id from ``telegram.env`` (KEY=VALUE env file),
    falling back to a bare one-line ``telegram_chat_id`` file.
    """
    env_file = paths.config / "telegram.env"
    try:
        if env_file.exists():
            for raw in env_file.read_text(encoding="utf-8").splitlines():
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if k.strip() == "TELEGRAM_CHAT_ID":
                    v = v.strip().strip('"').strip("'")
                    if v:
                        return v
    except OSError:
        pass

    candidates = [
        paths.config / "telegram_chat_id",
        paths.root / "telegram_chat_id",
    ]
    for c in candidates:
        try:
            if c.exists():
                v = c.read_text(encoding="utf-8").strip()
                if v:
                    return v
        except OSError:
            continue
    return None


def _log_telegram_error(paths: Paths, msg: str) -> None:
    try:
        log = _telegram_error_log(paths)
        log.parent.mkdir(parents=True, exist_ok=True)
        ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {msg}\n")
    except OSError:
        pass


# ---------- activity tracking ----------

def track_turn_completion(agent: str, paths: Paths) -> None:
    """Bump the per-agent activity counter under flock and update
    ``updated_at``. Logs an ``agent.heartbeat`` event every 10 turns.
    """
    agent_dir = paths.agent_dir(agent)
    try:
        agent_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return

    activity_file = agent_dir / "activity.json"
    turns = 0
    # One lock spans the activity-json + status + updated_at writes so a
    # racing posthook from a sibling process can never observe a
    # half-updated agent state; the explicit lock ensures atomicity.
    try:
        with file_lock(activity_file):
            data: dict[str, Any] = {}
            if activity_file.exists():
                try:
                    data = json.loads(activity_file.read_text(encoding="utf-8")) or {}
                except (OSError, json.JSONDecodeError):
                    data = {}
            turns = int(data.get("turns") or 0) + 1
            now = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            data["turns"] = turns
            data["updated_at"] = now
            atomic_write_text(activity_file, json.dumps(data, indent=2, sort_keys=True) + "\n")

            # Upgrade "spawned" → "active" + bump updated_at marker.
            status_file = agent_dir / "status"
            if status_file.exists():
                try:
                    cur = status_file.read_text(encoding="utf-8").strip()
                    if cur == "spawned":
                        atomic_write_text(status_file, "active\n")
                except OSError:
                    pass
            try:
                atomic_write_text(agent_dir / "updated_at", now + "\n")
            except OSError:
                pass
    except OSError:
        return

    if turns > 0 and turns % 10 == 0:
        try:
            log_event(
                "agent.heartbeat",
                f"{agent} turn {turns}",
                agent=agent,
                paths=paths,
            )
        except Exception:  # noqa: BLE001
            pass


# ---------- ephemeral-agent task auto-close ----------

def auto_close_finished_task(agent: str, paths: Paths) -> str | None:
    """If ``agent`` has a linked task and its status indicates a clean
    completion, archive the task. Returns the closed task slug, or None.

    Conditions for auto-close (all must hold):
      - ``agent_dir/task_id`` exists and names an active task
      - ``agent_dir/status`` exists and starts with ``complete``
        (the harness completion protocol writes ``complete: <summary>``)
      - the task is not already archived

    Edge cases:
      - panic / error exit → status will not start with ``complete``,
        so we leave the task pending for human triage
      - multiple agents share a task → last writer wins, harmless
      - task already archived → ``_find_task_file`` returns None, no-op
      - missing ``task_id`` (legacy spawns from before this fix) → no-op
    """
    agent_dir = paths.agent_dir(agent)
    task_id_file = agent_dir / "task_id"
    status_file = agent_dir / "status"
    if not task_id_file.exists() or not status_file.exists():
        return None
    try:
        task_id = task_id_file.read_text(encoding="utf-8").strip()
        status = status_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    if not task_id or not status.lower().startswith("complete"):
        return None

    # Local imports avoid pulling tasks → io chain into every Stop tick
    # and dodge any potential cycle through the cli shims.
    from . import tasks as _tasks

    active_path = _tasks._find_task_file(task_id, paths.project_root, include_completed=False)
    if active_path is None:
        return None
    if active_path.parent.name != "active":
        # Already archived (somehow). No-op, but clear the linkage so
        # we don't keep retrying every turn.
        return None

    summary = status.split(":", 1)[1].strip() if ":" in status else status
    try:
        _tasks.complete_task(task_id, f"auto-closed by posthook: {summary}", paths.project_root)
    except Exception:  # noqa: BLE001
        return None
    return task_id


# ---------- deferred slash-command injection ----------

def _check_deferred_command(agent: str, paths: Paths) -> None:
    """If the agent left a deferred command marker, inject it into the
    tmux session as the next user input.

    This lets agents request ``/exit`` (or any slash command) from within
    their assistant response. The posthook runs *after* the turn is
    complete, so the injected text becomes a fresh user message.

    Marker: ``state/<agent>_deferred_cmd`` containing the command text.
    Consumed on read (deleted immediately).
    """
    safe_name = agent.lstrip("@") or "orchestrator"
    marker = paths.state / f"{safe_name}_deferred_cmd"
    try:
        if not marker.exists():
            return
        cmd = marker.read_text(encoding="utf-8").strip()
        marker.unlink(missing_ok=True)
        if not cmd:
            return
    except OSError:
        return

    try:
        from .tmux import submit_to_tmux
        from .agents import session_name_for

        session = session_name_for(agent)
        submit_to_tmux(session, cmd)
    except Exception:  # noqa: BLE001
        pass


def request_deferred_command(cmd: str, paths: Paths | None = None, agent: str | None = None) -> None:
    """Write a deferred command marker so the next posthook tick injects
    ``cmd`` into the agent's tmux session.

    Usage from agent code::

        from metasphere.posthook import request_deferred_command
        request_deferred_command("/exit")
    """
    paths = paths or resolve()
    agent = agent or resolve_agent_id(paths)
    safe_name = agent.lstrip("@") or "orchestrator"
    marker = paths.state / f"{safe_name}_deferred_cmd"
    try:
        marker.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_text(marker, cmd + "\n")
    except OSError:
        pass


# ---------- top-level entry ----------

def run_posthook(stdin_bytes: bytes, paths: Paths | None = None) -> int:
    """Top-level Stop-hook entry point. Always returns 0."""
    try:
        paths = paths or resolve()
        payload = read_stop_hook_payload(stdin_bytes)

        agent = resolve_agent_id(paths)

        # Re-entrancy guard: bail if claude-code is already inside a Stop hook.
        already_active = bool(payload.get("stop_hook_active"))

        if not already_active and agent == "@orchestrator":
            transcript = payload.get("transcript_path")
            if transcript:
                text = extract_last_assistant_text(Path(transcript))
                if not should_skip_silent_tick(text):
                    # Suppress if @orchestrator already sent something via
                    # `metasphere-telegram send` during this turn — the
                    # final assistant text is almost certainly a recap and
                    # the user gets the same content twice. The CLI drops a
                    # marker file with mtime=now on every explicit send.
                    if not _explicit_send_marker_fresh(paths):
                        route_to_telegram(text or "", paths)
                    # Either way the user got something user-visible this
                    # turn — flip 👀 → 👍 on the message that triggered it.
                    try:
                        consume_pending_ack(paths)
                    except Exception:  # noqa: BLE001
                        pass

        track_turn_completion(agent, paths)

        # Auto-close the agent's backing task when an ephemeral agent
        # has finished cleanly. This is the fix for the 2026-04-08
        # backlog drift: previously, ephemeral agents had no
        # task↔agent linkage, so finished work piled up as
        # stale-pending tasks. spawn_ephemeral now writes task_id;
        # this hook closes it on clean exit.
        if agent != "@orchestrator":
            try:
                auto_close_finished_task(agent, paths)
            except Exception:  # noqa: BLE001
                pass

        # Check for deferred slash commands (/exit, etc.) that the agent
        # requested during its turn. Must run last — if the command is
        # /exit, the session will terminate shortly after injection.
        try:
            _check_deferred_command(agent, paths)
        except Exception:  # noqa: BLE001
            pass
    except Exception:  # noqa: BLE001 — Stop hook must never break the host
        try:
            paths = paths or resolve()
            _log_telegram_error(paths, "run_posthook crash: " + traceback.format_exc())
        except Exception:  # noqa: BLE001
            pass
    return 0


# ---------- CLI entry point ----------


def main() -> int:
    """CLI entry for the Stop hook. Reads the payload from stdin."""
    import sys
    stdin_bytes = sys.stdin.buffer.read()
    return run_posthook(stdin_bytes)


if __name__ == "__main__":
    raise SystemExit(main())
