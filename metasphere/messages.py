"""Fractal inter-agent messaging.

Atomic read-modify-write under flock (see :mod:`metasphere.io`).
Every message
is a YAML-frontmatter file at ``<scope>/.messages/inbox/<id>.msg``;
sender keeps a copy in ``<scope>/.messages/outbox/<id>.msg``.

Visibility is upward-fractal: an agent at ``/a/b/`` sees messages in
``/a/b/.messages/inbox`` AND every parent ``.messages/inbox`` up to
the repo root.
"""

from __future__ import annotations

import datetime as _dt
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .events import log_event
from .io import (
    Frontmatter,
    file_lock,
    read_frontmatter_file,
    read_json,
    write_frontmatter_file,
    write_json,
)
from .paths import Paths, rel_path as _rel_path, resolve  # noqa: F401  (re-export)

# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

STATUS_UNREAD = "unread"
STATUS_READ = "read"
STATUS_REPLIED = "replied"
STATUS_COMPLETED = "completed"

# Canonical frontmatter field order — preserved on every rewrite so the
# test 'update_status preserves frontmatter ordering' passes and human
# diffs stay readable.
_FIELD_ORDER = (
    "id",
    "from",
    "to",
    "label",
    "status",
    "scope",
    "created",
    "read_at",
    "replied_at",
    "completed_at",
    "reply_to",
    "last_pinged_at",
    "ping_count",
)

# Labels whose messages are pinned in the inbox: never auto-mark-read
# on view, never auto-archived. They represent work-to-do that requires
# an explicit human/agent action to unpin (complete).
PINNED_LABELS = frozenset({"!task", "!query"})

# Backward-compat alias (used in older code / tests).
SACRED_LABELS = PINNED_LABELS


def _utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Message:
    id: str
    from_: str = ""
    to: str = ""
    label: str = ""
    status: str = STATUS_UNREAD
    scope: str = "/"
    created: str = ""
    read_at: str = ""
    replied_at: str = ""
    completed_at: str = ""
    reply_to: str = ""
    last_pinged_at: str = ""
    ping_count: int = 0
    body: str = ""
    path: Path | None = None  # runtime only

    # ---- (de)serialisation ----

    def to_frontmatter(self) -> Frontmatter:
        meta = {
            "id": self.id,
            "from": self.from_,
            "to": self.to,
            "label": self.label,
            "status": self.status,
            "scope": self.scope,
            "created": self.created,
            "read_at": self.read_at,
            "replied_at": self.replied_at,
            "completed_at": self.completed_at,
            "reply_to": self.reply_to,
            "last_pinged_at": self.last_pinged_at,
            "ping_count": self.ping_count,
        }
        body = self.body if self.body.startswith("\n") else "\n" + self.body
        return Frontmatter(meta=meta, body=body)

    @classmethod
    def from_frontmatter(cls, fm: Frontmatter, path: Path | None = None) -> "Message":
        m = fm.meta
        def s(k: str) -> str:
            v = m.get(k)
            return "" if v is None else str(v)
        return cls(
            id=s("id"),
            from_=s("from"),
            to=s("to"),
            label=s("label"),
            status=s("status") or STATUS_UNREAD,
            scope=s("scope") or "/",
            created=s("created"),
            read_at=s("read_at"),
            replied_at=s("replied_at"),
            completed_at=s("completed_at"),
            reply_to=s("reply_to"),
            last_pinged_at=s("last_pinged_at"),
            ping_count=int(m.get("ping_count") or 0),
            # Preserve trailing whitespace/blank lines a sender deliberately
            # included; only normalise the leading newline that
            # ``serialize_frontmatter`` adds.
            body=fm.body.lstrip("\n") if fm.body else "",
            path=path,
        )


# ---------------------------------------------------------------------------
# IO
# ---------------------------------------------------------------------------


def read_message(path: Path, *, view: bool = False) -> Message:
    """Load a message from disk.

    When ``view=True``, this is a *view context* (e.g. inbox listing,
    per-turn context injection). Unread messages that are not labelled
    with a sacred label (``!task``/``!query``) get promoted to
    ``read`` and stamped with ``read_at`` in-place. This closes the
    "messages pile up forever as unread" feedback loop — every time
    the inbox is rendered, non-action messages get marked read, so
    the next tick doesn't re-show them.

    Sacred labels are preserved as unread because someone still needs
    to act on them; auto-marking them read would lose the signal.
    """
    path = Path(path)
    msg = Message.from_frontmatter(read_frontmatter_file(path), path=path)
    if view and msg.status == STATUS_UNREAD and msg.label not in SACRED_LABELS:
        try:
            with file_lock(_lock_path(path)):
                # Re-read inside the lock to avoid racing with another writer.
                fresh = Message.from_frontmatter(read_frontmatter_file(path), path=path)
                if fresh.status == STATUS_UNREAD and fresh.label not in SACRED_LABELS:
                    fresh.status = STATUS_READ
                    fresh.read_at = _utcnow()
                    write_frontmatter_file(path, fresh.to_frontmatter())
                    msg = fresh
        except Exception:
            # View-side mark-read is best-effort; never fail a read.
            pass
    return msg


def _lock_path(path: Path) -> Path:
    """Sidecar lock file with a stable inode that survives ``os.replace``.

    ``write_frontmatter_file`` uses tmp+rename, so the destination inode is
    swapped on every write — locking the destination directly would let two
    writers each end up holding flocks on different inodes. The sidecar
    file is never unlinked, so its inode stays put.
    """
    return path.with_name(path.name + ".lock")


def write_message(msg: Message, path: Path) -> None:
    path = Path(path)
    with file_lock(_lock_path(path)):
        write_frontmatter_file(path, msg.to_frontmatter())
    msg.path = path


def update_status(msg_path: Path, field: str, value: str) -> Message:
    """Atomically rewrite a single frontmatter field on a message file."""
    msg_path = Path(msg_path)
    if field not in _FIELD_ORDER:
        raise ValueError(f"unknown message field: {field!r}")
    with file_lock(_lock_path(msg_path)):
        msg = read_message(msg_path)
        attr = "from_" if field == "from" else field
        setattr(msg, attr, value)
        write_frontmatter_file(msg_path, msg.to_frontmatter())
        return msg


# ---------------------------------------------------------------------------
# Scope walking
# ---------------------------------------------------------------------------


def collect_inbox(scope: Path, project_root: Path, *, view: bool = False) -> list[Message]:
    """Walk ``scope`` and every parent up to ``project_root``, returning all
    messages found in their ``.messages/inbox`` directories. Newest first
    (sorted by filename descending)."""
    scope = Path(scope).resolve()
    project_root = Path(project_root).resolve()
    paths: list[Path] = []
    current = scope
    while True:
        inbox = current / ".messages" / "inbox"
        if inbox.is_dir():
            paths.extend(p for p in inbox.glob("*.msg") if p.is_file())
        if current == project_root or project_root not in current.parents:
            break
        current = current.parent
    paths.sort(key=lambda p: p.name, reverse=True)
    out: list[Message] = []
    for p in paths:
        try:
            out.append(read_message(p, view=view))
        except Exception:
            continue
    return out


# ---------------------------------------------------------------------------
# @-mention parsing
# ---------------------------------------------------------------------------


@dataclass
class Mention:
    name: str               # bare name without leading @
    type: str               # 'project' | 'agent' | 'unknown'
    raw: str                # original token, e.g. '@recurse'


# Match @name where name starts with a letter/digit/underscore and may
# contain letters, digits, ``_`` or ``-``. Must be at start-of-string or
# preceded by whitespace/punctuation so we don't grab emails.
_MENTION_RE = re.compile(r"(?:(?<=^)|(?<=[\s,;:!?()\[\]{}]))@([A-Za-z0-9_][A-Za-z0-9_\-]*)")


def _project_names(paths: Paths | None) -> set[str]:
    paths = paths or resolve()
    try:
        data = read_json(paths.root / "projects.json", default=[]) or []
    except Exception:
        return set()
    return {str(e.get("name")) for e in data if e.get("name")}


def _agent_exists(name: str, paths: Paths | None) -> bool:
    paths = paths or resolve()
    return (paths.agents / f"@{name}").is_dir()


def extract_mentions(text: str, *, paths: Paths | None = None) -> list[Mention]:
    """Extract ``@<name>`` mentions from ``text``.

    Resolution order (per project-mentions feedback memory): the project
    registry at ``~/.metasphere/projects.json`` wins; otherwise check
    the agents directory; otherwise mark ``unknown``.
    """
    if not text:
        return []
    projects = _project_names(paths)
    out: list[Mention] = []
    seen: set[str] = set()
    for m in _MENTION_RE.finditer(text):
        name = m.group(1)
        if name in seen:
            continue
        seen.add(name)
        if name in projects:
            kind = "project"
        elif _agent_exists(name, paths):
            kind = "agent"
        else:
            kind = "unknown"
        out.append(Mention(name=name, type=kind, raw=f"@{name}"))
    return out


# ---------------------------------------------------------------------------
# Target resolution
# ---------------------------------------------------------------------------


def resolve_target(target: str, scope: Path, project_root: Path, paths: Paths | None = None) -> Path:
    """Resolve an ``@target`` string to an absolute scope directory.

    Resolution rules:

      * ``@.``     -> current scope
      * ``@..``    -> parent of scope
      * ``@/p/``   -> absolute filesystem path ``/p`` if that directory
                      exists; otherwise ``<project_root>/p`` (legacy
                      repo-relative form).
      * ``@name``  -> ``<metasphere>/agents/@name/scope`` if registered,
                      else repo root.
    """
    scope = Path(scope)
    project_root = Path(project_root)
    if not target:
        return scope
    if target == "@.":
        return scope
    if target == "@..":
        return scope.parent
    if target.startswith("@/"):
        rest = target[2:]
        if rest:
            abs_candidate = Path("/" + rest)
            if abs_candidate != Path("/") and abs_candidate.is_dir():
                return abs_candidate
        return project_root / rest.lstrip("/")
    if target.startswith("@"):
        paths = paths or resolve()
        scope_file = paths.agents / target / "scope"
        if scope_file.is_file():
            try:
                v = scope_file.read_text(encoding="utf-8").strip()
                if v:
                    return Path(v)
            except OSError:
                pass
        return project_root
    return scope


# ---------------------------------------------------------------------------
# Send / reply / done
# ---------------------------------------------------------------------------


_pid = os.getpid()
_id_lock = threading.Lock()
_last_epoch = 0


def _gen_msg_id() -> str:
    """Generate a canonical ``msg-<epoch>-<pid>`` message ID.

    To preserve per-second uniqueness within a process, we serialise
    callers via ``_id_lock`` and busy-wait until the wall clock advances
    if two sends arrive in the same second. Cross-process collisions are
    avoided by the embedded pid.
    """
    global _last_epoch
    with _id_lock:
        epoch = int(time.time())
        while epoch <= _last_epoch:
            time.sleep(0.01)
            epoch = int(time.time())
        _last_epoch = epoch
    return f"msg-{epoch}-{_pid}"


# ---------------------------------------------------------------------------
# Inbox index (msg_id → path) — avoids the O(N) repo walk in _find_inbox_msg.
# ---------------------------------------------------------------------------


def _index_path(paths: Paths) -> Path:
    return paths.state / "msg_index.json"


def _index_add(msg_id: str, path: Path, paths: Paths) -> None:
    idx_path = _index_path(paths)
    try:
        idx = read_json(idx_path, {}) or {}
        idx[msg_id] = str(path)
        write_json(idx_path, idx)
    except Exception:
        # Index is a perf cache; failures must not break message sends.
        pass


def _index_lookup(msg_id: str, paths: Paths) -> Path | None:
    try:
        idx = read_json(_index_path(paths), {}) or {}
        cand = idx.get(msg_id)
        if cand:
            p = Path(cand)
            if p.exists():
                return p
    except Exception:
        pass
    return None


def _ensure_dirs(*dirs: Path) -> None:
    for d in dirs:
        Path(d).mkdir(parents=True, exist_ok=True)


def send_message(
    target: str,
    label: str,
    body: str,
    from_agent: str,
    paths: Paths | None = None,
    *,
    reply_to: str = "",
    wake: bool = True,
) -> Message:
    """Write a new message to ``target``'s inbox + sender's outbox."""
    paths = paths or resolve()
    target_path = resolve_target(target, paths.scope, paths.project_root, paths=paths)
    target_inbox = target_path / ".messages" / "inbox"
    my_outbox = paths.scope / ".messages" / "outbox"
    _ensure_dirs(target_inbox, my_outbox)

    msg_id = _gen_msg_id()
    msg = Message(
        id=msg_id,
        from_=from_agent,
        to=target,
        label=label,
        status=STATUS_UNREAD,
        scope=_rel_path(target_path, paths.project_root),
        created=_utcnow(),
        reply_to=reply_to,
        body="\n" + body.rstrip() + "\n",
    )

    inbox_file = target_inbox / f"{msg_id}.msg"
    outbox_file = my_outbox / f"{msg_id}.msg"
    write_message(msg, inbox_file)
    # Outbox is a sender-side copy; safe to write the same content.
    write_frontmatter_file(outbox_file, msg.to_frontmatter())
    _index_add(msg_id, inbox_file, paths)

    try:
        log_event(
            "message.send",
            f"{from_agent} → {target}: {label}",
            agent=from_agent,
            meta={"msg_id": msg_id},
            paths=paths,
        )
    except Exception:
        pass

    if wake and from_agent != "@user":
        try:
            wake_recipient_if_live(target, label, from_agent, body, paths=paths)
        except Exception:
            pass

    # Mirror to project telegram topic (additive). Failures are silent —
    # regular fractal scope routing above is the source of truth.
    try:
        from . import project as _project
        _project.mirror_message_to_project_topic(
            target_path, label, body, from_agent, paths=paths,
        )
    except Exception:
        pass

    msg.path = inbox_file
    return msg


def _find_inbox_msg(
    msg_id: str, project_root: Path, paths: Paths | None = None
) -> Path | None:
    # Fast path: write-through index in ~/.metasphere/state/msg_index.json.
    if paths is not None:
        hit = _index_lookup(msg_id, paths)
        if hit is not None:
            return hit
    # Slow path: walk the repo for messages not present in the index.
    project_root = Path(project_root)
    for inbox in project_root.rglob(".messages/inbox"):
        cand = inbox / f"{msg_id}.msg"
        if cand.exists():
            if paths is not None:
                _index_add(msg_id, cand, paths)
            return cand
    return None


def reply_to_message(
    orig_id: str,
    body: str,
    from_agent: str,
    paths: Paths | None = None,
) -> Message:
    paths = paths or resolve()
    orig_path = _find_inbox_msg(orig_id, paths.project_root, paths=paths)
    if orig_path is None:
        raise FileNotFoundError(f"message {orig_id} not found")

    with file_lock(_lock_path(orig_path)):
        orig = read_message(orig_path)
        orig.status = STATUS_REPLIED
        orig.replied_at = _utcnow()
        write_frontmatter_file(orig_path, orig.to_frontmatter())

    return send_message(
        orig.from_, "!reply", body, from_agent, paths=paths, reply_to=orig_id
    )


def mark_done(
    orig_id: str,
    note: str,
    from_agent: str,
    paths: Paths | None = None,
) -> Message | None:
    """Mark a message completed; if ``note`` is given, send a !done back."""
    paths = paths or resolve()
    orig_path = _find_inbox_msg(orig_id, paths.project_root, paths=paths)
    if orig_path is None:
        raise FileNotFoundError(f"message {orig_id} not found")

    with file_lock(_lock_path(orig_path)):
        orig = read_message(orig_path)
        orig.status = STATUS_COMPLETED
        orig.completed_at = _utcnow()
        write_frontmatter_file(orig_path, orig.to_frontmatter())

    if note:
        return send_message(
            orig.from_, "!done", note, from_agent, paths=paths, reply_to=orig_id
        )
    return None


def scan_inbox_messages(project_root: Path) -> list[Message]:
    """Return every message currently in any ``.messages/inbox/`` under the repo.

    Mirrors :func:`metasphere.consolidate.scan_active_tasks`: used by
    the lifecycle consolidator to drive verdict classification.
    """
    project_root = Path(project_root).resolve()
    out: list[Message] = []
    for msg_dir in project_root.rglob(".messages"):
        inbox = msg_dir / "inbox"
        if not inbox.is_dir():
            continue
        for f in sorted(inbox.glob("*.msg")):
            try:
                out.append(read_message(f))
            except Exception:
                continue
    return out


def bump_ping(msg_path: Path, ping_count: int) -> Message:
    """Set ``last_pinged_at=now`` and increment ``ping_count`` in place."""
    msg_path = Path(msg_path)
    with file_lock(_lock_path(msg_path)):
        msg = read_message(msg_path)
        msg.last_pinged_at = _utcnow()
        msg.ping_count = (ping_count or 0) + 1
        write_frontmatter_file(msg_path, msg.to_frontmatter())
        return msg


def archive_message(msg_path: Path) -> Path:
    """Move a message out of ``inbox/`` into ``archive/YYYY-MM-DD/``.

    Returns the destination path. Safe to call on any message (unread,
    read, completed) — the mover doesn't inspect state.
    """
    msg_path = Path(msg_path)
    inbox = msg_path.parent
    msgs_dir = inbox.parent  # .messages/
    today = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d")
    archive_dir = msgs_dir / "archive" / today
    archive_dir.mkdir(parents=True, exist_ok=True)
    dest = archive_dir / msg_path.name
    # Avoid clobber if a same-id archive already exists.
    if dest.exists():
        return dest
    os.replace(str(msg_path), str(dest))
    # Clean up sidecar lock to avoid orphans in inbox/.
    lock = _lock_path(msg_path)
    try:
        if lock.exists():
            os.remove(lock)
    except OSError:
        pass
    return dest


def mark_read(msg_id: str, paths: Paths | None = None) -> Message:
    paths = paths or resolve()
    p = _find_inbox_msg(msg_id, paths.project_root, paths=paths)
    if p is None:
        raise FileNotFoundError(f"message {msg_id} not found")
    with file_lock(_lock_path(p)):
        msg = read_message(p)
        if msg.status == STATUS_UNREAD:
            msg.status = STATUS_READ
            msg.read_at = _utcnow()
            write_frontmatter_file(p, msg.to_frontmatter())
        return msg


# ---------------------------------------------------------------------------
# Wake (tmux plumbing stays in bash)
# ---------------------------------------------------------------------------


def wake_recipient_if_live(
    target: str,
    label: str,
    from_agent: str,
    body: str,
    paths: Paths | None = None,
) -> None:
    """Best-effort wake via :mod:`metasphere.tmux`. Failures are silent."""
    from .tmux import submit_to_tmux as _tmux_submit

    paths = paths or resolve()
    agent_name: str | None = None
    if target == "@..":
        if paths.scope.resolve() == paths.project_root.resolve():
            agent_name = "orchestrator"
    elif target.startswith("@/") or target == "@.":
        resolved = resolve_target(target, paths.scope, paths.project_root, paths=paths)
        if resolved.resolve() == paths.project_root.resolve():
            agent_name = "orchestrator"
    elif target.startswith("@"):
        agent_name = target[1:]

    if not agent_name:
        return

    session = f"metasphere-{agent_name}"
    body_preview = body[:200] + ("..." if len(body) > 200 else "")
    notice = f"[wake] new {label} from {from_agent}: {body_preview}"

    try:
        _tmux_submit(session, notice)
    except Exception:
        pass

    try:
        log_event(
            "agent.wake",
            f"@{agent_name} woken by {from_agent} ({label})",
            agent=from_agent,
            paths=paths,
        )
    except Exception:
        pass
