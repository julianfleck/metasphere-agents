"""Per-turn context block — Python port of scripts/metasphere-context.

The bash hook is what claude-code currently invokes via UserPromptSubmit;
this module is a parallel implementation. The cutover (replacing the
symlink in ``~/.metasphere/bin``) happens later.

Section order is load-bearing: the orchestrator's per-turn cognition
keys off the layout, so we match the bash byte-for-byte where it
matters (status header → drift warning → telegram → messages → tasks
→ events → FTS).

Pure stdlib. No third-party deps.
"""

from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import subprocess
from pathlib import Path
from typing import Iterable

from . import messages as _msgs
from . import tasks as _tasks
from .identity import resolve_agent_id
from .paths import Paths, rel_path, resolve

# Files baked into the REPL at session start. Order is irrelevant —
# the bash recipe sorts before concatenating, and so do we.
_HARNESS_FILES = (
    "CLAUDE.md",
    ".claude/settings.json",
    ".claude/settings.local.json",
    "scripts/metasphere-context",
)

DEFAULT_SECTION_BUDGET = 2048


# ---------------------------------------------------------------------------
# Truncation
# ---------------------------------------------------------------------------


def truncate_section(text: str, budget: int = DEFAULT_SECTION_BUDGET) -> str:
    """Cap a section to ``budget`` bytes, appending a truncation note."""
    if budget <= 0 or text is None:
        return ""
    data = text.encode("utf-8")
    if len(data) <= budget:
        return text
    cut = data[:budget].decode("utf-8", errors="ignore").rstrip()
    return cut + f"\n_(truncated: {len(data)} bytes total)_\n"


# ---------------------------------------------------------------------------
# Harness drift detector (PORTING invariant #8)
# ---------------------------------------------------------------------------


def _existing_harness_files(repo_root: Path) -> list[Path]:
    out: list[Path] = []
    for rel in _HARNESS_FILES:
        p = repo_root / rel
        if p.is_file():
            out.append(p)
    return out


def harness_hash(paths: Paths) -> str:
    """Sha256 of the harness files, sorted by path then concatenated.

    Matches the bash recipe in scripts/metasphere-context lines 49-87:

        printf '%s\\n' files | sort | xargs cat | sha256sum

    Returns "" if no files exist (matches bash empty-hash branch).
    """
    files = _existing_harness_files(paths.repo)
    if not files:
        return ""
    files_sorted = sorted(str(p) for p in files)
    h = hashlib.sha256()
    for fp in files_sorted:
        try:
            with open(fp, "rb") as f:
                h.update(f.read())
        except OSError:
            continue
    return h.hexdigest()


def _baseline_hash(paths: Paths) -> str:
    p = paths.state / "harness_hash_baseline"
    try:
        return p.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


# ---------------------------------------------------------------------------
# Section renderers
# ---------------------------------------------------------------------------


_STATUS_ICON = {
    _msgs.STATUS_UNREAD: "○",
    _msgs.STATUS_READ: "◐",
    _msgs.STATUS_REPLIED: "◑",
    _msgs.STATUS_COMPLETED: "●",
}


def _render_status_header(paths: Paths, agent: str) -> str:
    agent_dir = paths.agent_dir(agent)
    status = "unknown"
    sf = agent_dir / "status"
    if sf.is_file():
        try:
            status = sf.read_text(encoding="utf-8").strip() or "unknown"
        except OSError:
            pass
    out = [f"# Metasphere Delta ({agent})", "", f"_Status: {status}_", ""]
    return "\n".join(out)


def _render_drift_warning(paths: Paths) -> str:
    baseline = _baseline_hash(paths)
    if not baseline:
        return ""
    live = harness_hash(paths)
    if not live or live == baseline:
        return ""
    hash_file = paths.state / "harness_hash_baseline"
    return (
        "## ⚠ Harness drift detected\n"
        "\n"
        "Your baked-in CLAUDE.md / .claude/settings / metasphere-context has changed\n"
        "since this REPL started. The new content is on disk but you're still running\n"
        "the old version in memory.\n"
        "\n"
        "If the change is relevant to what you're doing (e.g. updated workflow,\n"
        "new slash command, new safety rule), run `/exit` — the tmux pane will\n"
        "respawn a fresh REPL with the latest harness. Otherwise ignore this and\n"
        "the warning will keep appearing until you reload.\n"
        "\n"
        f"_(suppress: `echo $_live_hash > {hash_file}` to silence without reloading)_\n"
    )


_TELEGRAM_BYTE_CAP = 1024


def _render_telegram(paths: Paths, history: int = 3) -> str:
    """Render the recent telegram conversation.

    Mirrors the bash hook (scripts/metasphere-context ~88-105): shells out to
    ``scripts/metasphere-telegram-stream context --history 3`` and caps at
    1024 bytes. Falls back to inline JSONL parsing only if the script is
    missing — that keeps test environments without the bash side working.
    """
    streamer = paths.repo / "scripts" / "metasphere-telegram-stream"
    if streamer.is_file() and os.access(streamer, os.X_OK):
        try:
            res = subprocess.run(
                [str(streamer), "context", "--history", str(history)],
                capture_output=True,
                text=True,
                timeout=5,
            )
            body = res.stdout
        except (subprocess.SubprocessError, OSError):
            body = ""
        if not body.strip():
            return ""
        data = body.encode("utf-8")[:_TELEGRAM_BYTE_CAP]
        return data.decode("utf-8", errors="ignore").rstrip() + "\n"

    # Fallback: parse today's archive directly.
    today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    archive = paths.telegram_stream / f"{today}.jsonl"
    if not archive.is_file():
        return ""
    try:
        lines = archive.read_text(encoding="utf-8").splitlines()
    except OSError:
        return ""
    # The bash version uses `tail -n` over JSON-lines but the file actually
    # contains pretty-printed JSON for outgoing messages. Be defensive: walk
    # the file as a stream of objects, then take the last `history` of them.
    objs = _parse_jsonl_loose(lines)
    if not objs:
        return ""
    objs = objs[-history:]
    out = ["## Telegram (recent conversation)", ""]
    for o in objs:
        frm_field = o.get("from")
        if isinstance(frm_field, dict):
            frm = frm_field.get("username") or frm_field.get("first_name") or "unknown"
        elif isinstance(frm_field, str):
            frm = frm_field
        else:
            frm = "unknown"
        text = o.get("text") or ""
        if not text:
            continue
        date_ts = o.get("date") or 0
        try:
            ts = _dt.datetime.fromtimestamp(float(date_ts), _dt.timezone.utc).strftime("%H:%M")
        except (TypeError, ValueError, OSError):
            ts = ""
        direction = "→" if o.get("outgoing") else "←"
        out.append(f"{direction} **@{frm}** ({ts}): {text}")
    out.append("")
    out.append('_Reply via: `metasphere-telegram send "message"`_')
    return "\n".join(out) + "\n"


def _parse_jsonl_loose(lines: Iterable[str]) -> list[dict]:
    """Parse a file that mixes single-line JSONL with pretty-printed objects.

    The bash archiver writes single-line JSONL, but earlier code (and the
    outgoing-archive path) sometimes wrote pretty-printed records. We
    accumulate brace-balanced chunks and json.loads each one.
    """
    out: list[dict] = []
    buf: list[str] = []
    depth = 0
    for line in lines:
        if not line.strip() and not buf:
            continue
        buf.append(line)
        depth += line.count("{") - line.count("}")
        if depth <= 0 and buf:
            chunk = "\n".join(buf).strip()
            buf = []
            depth = 0
            if not chunk:
                continue
            try:
                obj = json.loads(chunk)
                if isinstance(obj, dict):
                    out.append(obj)
            except json.JSONDecodeError:
                continue
    return out


def _render_messages(paths: Paths) -> str:
    msgs = _msgs.collect_inbox(paths.scope, paths.repo)
    unread = sum(1 for m in msgs if m.status == _msgs.STATUS_UNREAD)
    total = len(msgs)
    if total == 0:
        return "## Messages: No messages in scope\n"
    out = [
        f"## Messages ({unread} unread, {total} total)",
        f"## Scope: {rel_path(paths.scope, paths.repo)}",
        "",
    ]
    for m in msgs:
        if m.status != _msgs.STATUS_UNREAD:
            continue
        icon = _STATUS_ICON.get(m.status, "?")
        reply = f" ↩ reply to {m.reply_to}" if m.reply_to else ""
        body_preview = " ".join(m.body.split())[:60]
        out.append(f"{icon} {m.label} from {m.from_} [{m.id}]{reply}")
        out.append(f"  {m.scope} | {m.created}")
        out.append(f"  {body_preview}")
        out.append("")
    return "\n".join(out) + "\n"


def _render_tasks(paths: Paths) -> str:
    items = _tasks.list_tasks(paths.scope, paths.repo, include_completed=False)
    if not items:
        return "## Tasks: No active tasks in scope\n"
    out = [f"## Tasks ({len(items)} active)", ""]
    for t in items:
        icon = {
            "pending": "○",
            "in-progress": "◐",
            "blocked": "◼",
            "completed": "●",
        }.get(t.status, "?")
        suffix = f" → {t.assignee}" if t.assignee else ""
        out.append(f"{icon} {t.priority} {t.title} [{t.id}]{suffix}")
        out.append(f"  {t.scope} | {t.status}")
    return "\n".join(out) + "\n"


def _render_events(paths: Paths, n: int = 10) -> str:
    log = paths.events_log
    if not log.is_file():
        return "## Recent Events\n(no recent events)\n"
    try:
        with open(log, "r", encoding="utf-8") as f:
            lines = f.readlines()
    except OSError:
        return "## Recent Events\n(no recent events)\n"
    tail = lines[-n:]
    out = ["## Recent Events", ""]
    for line in tail:
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        ts = rec.get("timestamp", "")
        typ = rec.get("type", "")
        msg = rec.get("message", "")
        out.append(f"- {ts} [{typ}] {msg}")
    return "\n".join(out) + "\n"


def _render_memory_fts(paths: Paths, agent: str) -> str:
    """Shell out to ``scripts/metasphere-fts`` if present; else skip cleanly."""
    fts = paths.repo / "scripts" / "metasphere-fts"
    out = ["## Memory Context (FTS)"]
    if not fts.is_file() or not os.access(fts, os.X_OK):
        out.append("metasphere-fts not found. (Tip: `cam search <query>` for deeper recall.)")
        return "\n".join(out) + "\n"

    # Build query from the agent's task file + repo basename, mirroring bash.
    task_file = paths.agent_dir(agent) / "task"
    query_parts: list[str] = []
    if task_file.is_file():
        try:
            query_parts.append(task_file.read_text(encoding="utf-8").strip())
        except OSError:
            pass
    query_parts.append(paths.repo.name)
    query = " ".join(p for p in query_parts if p).replace("\n", " ")
    query = " ".join(query.split())[:200] or agent

    try:
        res = subprocess.run(
            [str(fts), query, "5"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        body = res.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        body = ""
    if not body:
        body = "No relevant memory found."
    out.append(body)
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------------------
# Top-level assembly
# ---------------------------------------------------------------------------


SECTION_NAMES = (
    "status",
    "drift",
    "telegram",
    "messages",
    "tasks",
    "events",
    "memory",
)


def build_context(paths: Paths | None = None, *, budget: int = DEFAULT_SECTION_BUDGET) -> str:
    """Assemble the per-turn context block. Section order is load-bearing."""
    paths = paths or resolve()
    agent = resolve_agent_id(paths)

    sections: list[str] = []

    sections.append(truncate_section(_render_status_header(paths, agent), budget))
    drift = _render_drift_warning(paths)
    sections.append(truncate_section(drift, budget) if drift else "")
    sections.append(truncate_section(_render_telegram(paths), budget))
    sections.append(truncate_section(_render_messages(paths), budget))
    sections.append(truncate_section(_render_tasks(paths), budget))
    sections.append(truncate_section(_render_events(paths), budget))
    sections.append(truncate_section(_render_memory_fts(paths, agent), budget))

    return "\n".join(s for s in sections if s).rstrip() + "\n"
