"""Fractal inter-agent messaging.

Atomic read-modify-write under flock (see :mod:`metasphere.io`).
Every message is a YAML-frontmatter file at
``~/.metasphere/projects/<name>/.messages/inbox/<id>.msg``; sender
keeps a copy at ``.messages/outbox/<id>.msg`` in the same project
dir. For messages whose scope resolves to no registered project the
canonical location is ``~/.metasphere/messages/inbox/<id>.msg`` (the
global sentinel bucket).

Visibility is per-project: an agent looking at its inbox sees every
message in its project's ``.messages/inbox/`` plus the global bucket.
Pre-PR #10 the layout was per-scope nested (``<scope>/.messages/inbox``
walked up to the repo root); the migration subcommand moved those
into the canonical per-project tree and the walk collapsed to a
two-bucket lookup.
"""

from __future__ import annotations

import datetime as _dt
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)

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

# Labels that escalate to ``wake_persistent`` when the best-effort
# tmux inject in ``wake_recipient_if_live`` returns delivered=False
# (dormant session, deferred typing, unresolvable target). Without
# the escalation, !task can sit unread for hours when the recipient
# is dormant (a 7h-stuck !task incident drove this fix).
# !info, !done, and !reply intentionally stay on the heartbeat
# cadence: they're async status flow where REPL pickup on the next
# turn is the correct semantic.
HIGH_PRIORITY_LABELS = frozenset({"!task", "!urgent", "!query"})


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


def _canonical_inbox_dirs(paths_obj: Paths | None = None) -> list[Path]:
    """Every ``.messages/inbox/`` dir on the canonical layout.

    One per registered project plus the global bucket at
    ``~/.metasphere/messages/inbox/``. Replaces the pre-PR #10
    per-scope nested walk.
    """
    paths_obj = paths_obj or resolve()
    out: list[Path] = []
    if paths_obj.projects.is_dir():
        for entry in sorted(paths_obj.projects.iterdir()):
            inbox = entry / ".messages" / "inbox"
            if inbox.is_dir():
                out.append(inbox)
    global_inbox = paths_obj.root / "messages" / "inbox"
    if global_inbox.is_dir():
        out.append(global_inbox)
    return out


def _canonical_messages_dirs(paths_obj: Paths | None = None) -> list[Path]:
    """Every canonical ``.messages/`` dir — one per registered project
    plus the global bucket.

    Used by :func:`_find_message_anywhere` to walk archive/outbox in
    addition to the live inbox set.
    """
    paths_obj = paths_obj or resolve()
    out: list[Path] = []
    if paths_obj.projects.is_dir():
        for entry in sorted(paths_obj.projects.iterdir()):
            msgs = entry / ".messages"
            if msgs.is_dir():
                out.append(msgs)
    global_msgs = paths_obj.root / "messages"
    if global_msgs.is_dir():
        out.append(global_msgs)
    return out


def _canonical_messages_dir(scope: Path, paths_obj: Paths) -> Path:
    """Resolve an arbitrary scope to its canonical ``.messages/`` dir.

    Scope → registered project → ``paths.projects/<name>/.messages``.
    Scope outside any registered project → the global bucket at
    ``paths.root/messages``.
    """
    from .project import Project
    proj = Project.for_cwd(Path(scope), paths_obj)
    if proj is not None and proj.name:
        return proj.messages_dir(paths_obj)
    return Project.global_scope().messages_dir(paths_obj)


def collect_inbox(scope: Path, project_root: Path, *, view: bool = False) -> list[Message]:
    """Return every message visible from ``scope``, newest first.

    Under the canonical layout (PR #10): the project that owns
    ``scope`` has exactly one ``.messages/inbox/``, plus there is a
    global bucket. Visibility = project + global. The old per-scope
    nested walk doesn't apply — subdirectories no longer carry their
    own inboxes.
    """
    paths_obj = resolve()
    scope = Path(scope).resolve()
    project_root = Path(project_root).resolve()  # accepted for signature compat

    msg_paths: list[Path] = []
    from .project import Project
    proj = Project.for_cwd(scope, paths_obj)
    candidates: list[Project] = []
    if proj is not None and proj.name:
        candidates.append(proj)
    candidates.append(Project.global_scope())
    for c in candidates:
        inbox = c.messages_dir(paths_obj) / "inbox"
        if inbox.is_dir():
            msg_paths.extend(p for p in inbox.glob("*.msg") if p.is_file())

    msg_paths.sort(key=lambda p: p.name, reverse=True)
    out: list[Message] = []
    for p in msg_paths:
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
    raw: str                # original token, e.g. '@example-project'


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
    # Canonical routing (PR #10): both target inbox and sender outbox
    # land under ``~/.metasphere/projects/<name>/.messages/`` (or the
    # global bucket if the scope doesn't resolve to a registered
    # project). The pre-refactor in-repo ``<scope>/.messages/`` tree
    # has been migrated by ``metasphere migrate-project-dirs --what messages``.
    target_inbox = _canonical_messages_dir(target_path, paths) / "inbox"
    my_outbox = _canonical_messages_dir(paths.scope, paths) / "outbox"
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

    # Outbound activity IS activity: refresh the sender's last_active
    # so the reapers' shared idle signal sees an agent that is quietly
    # WORKING (sending bus traffic) even when no successful inject has
    # landed for hours. 2026-07-05 21:05: @worker was stale-killed
    # on wake FOUR MINUTES after sending a !task — every wake since
    # ~18:10 had hit "submit failed" (queued, not submitted), so no
    # input-side signal ever registered while the agent worked on.
    # The no-create variant is load-bearing: synthetic senders
    # (@consolidate, @heartbeat, @scheduler, @posthook) have no agent
    # dir and must not get ghost dirs minted here.
    if (
        from_agent
        and from_agent.startswith("@")
        and from_agent not in ("@user", "@.", "@..")
        and "/" not in from_agent
    ):
        try:
            from . import agents as _agents
            _agents.touch_last_active_if_exists(from_agent, paths)
        except Exception:
            pass

    delivered = False
    if wake and from_agent != "@user":
        try:
            delivered = wake_recipient_if_live(
                target, label, from_agent, body, paths=paths,
            )
        except Exception:
            pass

    # Escalate to session-respawn for high-priority labels when the
    # best-effort tmux inject above didn't actually land. Without this,
    # a !task to a dormant recipient sits unread until the next idle
    # heartbeat tick (incident: a !task sat 7h unread on a
    # nominally-alive but unresponsive recipient before the operator
    # noticed). wake_persistent injects
    # into a live session OR cold-starts a fresh one; B1's truncation-
    # safe bootstrap-pointer handles long bodies automatically.
    if (
        wake
        and not delivered
        and label in HIGH_PRIORITY_LABELS
        and from_agent != "@user"
        and target != from_agent  # self-send guard
        and _is_wakeable_agent_target(target, paths)
    ):
        try:
            from . import agents as _agents
            agent_name = target[1:]  # strip leading @
            body_preview = ""
            if body.strip():
                body_preview = body.strip().splitlines()[0][:80]
            # msg_id is already 'msg-<unix-ms>-<rand>'; don't double-prefix.
            first_task = f"New {label} in inbox: {msg_id}."
            if body_preview:
                first_task = f"{first_task} {body_preview}"
            _agents.wake_persistent(agent_name, first_task=first_task, paths=paths)
        except ValueError:
            # wake_persistent raises ValueError when target isn't a
            # registered persistent agent (no MISSION.md). Expected for
            # ephemerals and stale @<name> references; inbox delivery
            # already happened above so nothing else to do.
            pass
        except Exception as e:
            logger.warning(
                "send_message(%s): high-priority wake_persistent raised: %s",
                target, e,
            )

    # Mirror to project telegram topic (additive). Failures are silent —
    # regular fractal scope routing above is the source of truth.
    try:
        from . import project as _project
        _project.mirror_message_to_project_topic(
            target_path, label, body, from_agent, paths=paths,
        )
    except Exception:
        pass

    # Session-hygiene hook: a !done from an ephemeral sender is the
    # single terminal signal in the message protocol. Kill its tmux
    # session (no-op if absent) and clear runtime state pointers so a
    # future spawn re-bootstraps cleanly. Persistent senders are a
    # strict no-op — their lifecycle is governed by idle-TTL dormancy.
    # Failures must not break message delivery — the message is already
    # written, indexed, and mirrored above.
    if label == "!done":
        try:
            from . import agents as _agents
            _agents.on_done_delivered(from_agent, paths=paths)
        except Exception:
            pass
        # Close the dispatch loop: if this !done references a dispatched
        # task (via the [task:<id>] tag dispatch_task embeds in the !task
        # message), auto-complete that task. Best-effort — delivery has
        # already happened, so a failure here must not propagate.
        try:
            _complete_dispatched_task_on_done(msg, from_agent, paths)
        except Exception:
            pass

    msg.path = inbox_file
    return msg


#: Matches the ``[task:<id>]`` tag :func:`metasphere.tasks.dispatch_task`
#: embeds in the body of the ``!task`` message it sends. The id is a
#: task slug (lowercase alphanumerics, ``-`` / ``_``).
_TASK_TAG_RE = re.compile(r"\[task:([a-zA-Z0-9_-]+)\]")


def _complete_dispatched_task_on_done(
    msg: Message, from_agent: str, paths: Paths
) -> None:
    """Auto-close the dispatched task a ``!done`` refers to.

    The linkage is the ``[task:<id>]`` tag ``dispatch_task`` embeds in
    the ``!task`` message body. It is resolved from (in order):

    1. the ``!done`` body itself (an agent may echo the tag), then
    2. the message this ``!done`` replies to (the original ``!task``) —
       the ``metasphere msg done <task-msg-id>`` path sets ``reply_to``.

    Auto-close is unconditional once the tag resolves to a still-open
    task — the dispatch tag makes the linkage unambiguous (orchestrator
    design call, 2026-06-21 audit fix #1). No-op when no tag resolves or
    the task is already closed/missing.
    """
    from . import tasks as _tasks

    task_id = None
    m = _TASK_TAG_RE.search(msg.body or "")
    if m:
        task_id = m.group(1)
    elif msg.reply_to:
        orig_path = _find_message_anywhere(msg.reply_to, paths=paths)
        if orig_path is not None:
            try:
                orig = read_message(orig_path)
                mm = _TASK_TAG_RE.search(orig.body or "")
                if mm:
                    task_id = mm.group(1)
            except Exception:
                task_id = None
    if not task_id:
        return

    # Only act on a task that is still open in active/ — never resurrect
    # or double-archive an already-completed task.
    open_path = _tasks._find_task_file(task_id, include_completed=False)
    if open_path is None:
        return

    summary = _TASK_TAG_RE.sub("", (msg.body or "")).strip()
    attestation = f"auto-closed via !done from {from_agent}"
    if summary:
        attestation += f": {summary}"
    _tasks.complete_task(task_id, attestation, paths.project_root)


def _find_inbox_msg(
    msg_id: str, project_root: Path, paths: Paths | None = None
) -> Path | None:
    # Fast path: write-through index in ~/.metasphere/state/msg_index.json.
    # The index is inbox-only (callers below operate on live inbox state);
    # archive-located entries are filtered out so write-side surfaces
    # (``reply_to_message``, ``mark_done``) never try to mutate archived
    # messages.
    if paths is not None:
        hit = _index_lookup(msg_id, paths)
        if hit is not None and hit.parent.name == "inbox":
            return hit
    # Slow path: walk the canonical per-project inboxes. The
    # ``project_root`` argument is retained for signature compat but
    # no longer used for path lookup — one inbox per project + a
    # global bucket is the full universe to check.
    for inbox in _canonical_inbox_dirs(paths):
        cand = inbox / f"{msg_id}.msg"
        if cand.exists():
            if paths is not None:
                _index_add(msg_id, cand, paths)
            return cand
    return None


def _find_message_anywhere(
    msg_id: str, paths: Paths | None = None,
) -> Path | None:
    """Locate a message by id anywhere on disk — inbox, archive, or
    outbox.

    Used by read-only surfaces (``metasphere msg read``) so a message
    remains discoverable across its full lifecycle: live in inbox →
    moved to archive by ``archive_message`` → or surviving only as a
    sender-side outbox copy when delivery failed. Write-side surfaces
    keep using :func:`_find_inbox_msg` to avoid mutating archived state.

    Search order: inbox first (most reads are of live messages), then
    archive (recently-completed reads), then outbox (sender-side
    queries + degenerate cases where the inbox copy never landed). The
    index is consulted as a fast-path for every layer — when the
    indexed path is stale (file moved out from under it) the walk
    re-discovers and self-heals the index entry.
    """
    paths = paths or resolve()

    # Fast path: index. Trust the cached path if it still exists.
    hit = _index_lookup(msg_id, paths)
    if hit is not None:
        return hit

    # Slow path: walk inbox, archive, outbox per canonical messages dir.
    for inbox in _canonical_inbox_dirs(paths):
        cand = inbox / f"{msg_id}.msg"
        if cand.exists():
            _index_add(msg_id, cand, paths)
            return cand

    for messages_dir in _canonical_messages_dirs(paths):
        archive_root = messages_dir / "archive"
        if archive_root.is_dir():
            # Newest day first so recently-archived messages resolve
            # quickly. Day dirs are YYYY-MM-DD so lexicographic sort
            # matches chronological.
            for day_dir in sorted(archive_root.iterdir(), reverse=True):
                cand = day_dir / f"{msg_id}.msg"
                if cand.exists():
                    _index_add(msg_id, cand, paths)
                    return cand

    for messages_dir in _canonical_messages_dirs(paths):
        outbox = messages_dir / "outbox"
        if outbox.is_dir():
            cand = outbox / f"{msg_id}.msg"
            if cand.exists():
                _index_add(msg_id, cand, paths)
                return cand

    return None


# ---------------------------------------------------------------------------
# Outbox-orphan sweep (silent-loss backstop)
#
# An outbox file with no inbox/archive twin is a message the bus never
# carried: nothing woke the recipient, nothing shows in any inbox view,
# and ``msg done`` can't resolve it — a silent loss. The 2026-07-05
# 20:52 incident was exactly this: an agent bypassed ``send_message``
# and hand-wrote its ``!done`` straight into its project outbox
# (believing outbox = send queue); the sign-off sat invisible for an
# hour. The sweep makes the outbox eventually-consistent with the
# inbox: any orphan inside the age window is late-delivered through
# the same primitives ``send_message`` uses, with a distinct
# ``message.orphan_delivered`` event so bypass/misuse stays VISIBLE
# instead of silently absorbed.
# ---------------------------------------------------------------------------

def _delivered_copy_exists(msg_id: str, paths: Paths) -> bool:
    """True iff ``msg_id`` has a copy anywhere OTHER than an outbox —
    live inbox or archive.

    Deliberately does NOT consult the index: a read-only surface that
    touched an orphan (``msg read`` walks outboxes last) indexes its
    OUTBOX path, and that entry must not count as delivered.
    """
    for inbox in _canonical_inbox_dirs(paths):
        if (inbox / f"{msg_id}.msg").exists():
            return True
    for messages_dir in _canonical_messages_dirs(paths):
        archive_root = messages_dir / "archive"
        if archive_root.is_dir():
            for day_dir in sorted(archive_root.iterdir(), reverse=True):
                if (day_dir / f"{msg_id}.msg").exists():
                    return True
    return False


def _file_age_seconds(path: Path) -> int | None:
    """Age of ``path`` in seconds by file MTIME; ``None`` if unstattable.

    Deliberately ignores the embedded ``created`` frontmatter: the
    orphan population is dominated by hand-written files whose
    frontmatter is copy-pasted from older messages. A copied
    ``created`` older than the sweep's max-age would orphan the
    message FOREVER behind a green backstop, and a backdated one
    would defeat the min-age quiescence guard (a mid-write partial
    file could be delivered corrupted, and idempotency would make
    that permanent). MTIME is the one signal the writer sets by the
    act of writing itself. ``created`` is display-only here.
    """
    try:
        return max(0, int(time.time() - path.stat().st_mtime))
    except OSError:
        return None


def sweep_outbox_orphans(
    paths: Paths | None = None,
    *,
    min_age_seconds: int = 300,
    max_age_seconds: int = 86400,
    max_deliveries: int = 25,
    dry_run: bool = False,
) -> list[dict]:
    """Late-deliver outbox-only orphan messages.

    Walks every canonical ``.messages/outbox/`` and, for each ``.msg``
    with no inbox or archive twin whose age falls inside
    ``[min_age_seconds, max_age_seconds]``, writes the inbox copy to
    the recipient's canonical inbox (same id — threading and
    ``reply_to`` references stay intact), indexes it, emits a
    ``message.orphan_delivered`` event, and best-effort wakes the
    recipient.

    Window semantics:
    - ``min_age_seconds`` keeps the sweep off in-flight sends (the
      outbox copy lands milliseconds after the inbox copy on the
      normal path, but don't race it).
    - ``max_age_seconds`` bounds the blast radius of the FIRST sweep
      over a backlog of historical orphans, and guards against
      re-delivering a message whose inbox+archive copies were removed
      by some future retention policy.
    - ``max_deliveries`` caps one sweep; the remainder lands on
      subsequent ticks (consolidate runs every ~5 min).

    Idempotent by construction: a delivered orphan has an inbox twin
    and is never picked up again. Per-file failures are swallowed —
    this runs on the consolidate tick and must never abort it. Returns
    one result dict per delivered (or would-deliver) orphan.
    """
    paths = paths or resolve()
    out: list[dict] = []
    delivered = 0
    for messages_dir in _canonical_messages_dirs(paths):
        outbox = messages_dir / "outbox"
        if not outbox.is_dir():
            continue
        for f in sorted(outbox.glob("*.msg")):
            if delivered >= max_deliveries:
                return out
            try:
                msg_id = f.stem
                # Age gate FIRST: one cheap stat filters the entire
                # population of legitimately-sent outbox files (age >
                # max or < min) before the multi-dir twin scan — the
                # steady-state tick cost stays one stat per file.
                age = _file_age_seconds(f)
                if age is None or age < min_age_seconds or age > max_age_seconds:
                    continue
                if _delivered_copy_exists(msg_id, paths):
                    continue
                msg = read_message(f)
                if not msg.to or not msg.to.startswith("@"):
                    # No routable recipient — leave it; a fabricated file
                    # with a garbage ``to`` has no inbox to land in.
                    continue

                result = {
                    "action": "would-orphan-deliver" if dry_run else "orphan-delivered",
                    "msg_id": msg_id,
                    "from": msg.from_,
                    "to": msg.to,
                    "label": msg.label,
                    "age_seconds": age,
                }
                if dry_run:
                    out.append(result)
                    continue

                target_path = resolve_target(
                    msg.to, paths.scope, paths.project_root, paths=paths,
                )
                target_inbox = _canonical_messages_dir(target_path, paths) / "inbox"
                _ensure_dirs(target_inbox)
                inbox_file = target_inbox / f"{msg_id}.msg"
                write_message(msg, inbox_file)
                _index_add(msg_id, inbox_file, paths)
                delivered += 1

                # NOTE: the ``agent=`` attribution below repeats the
                # file's self-declared ``from`` field — for hand-written
                # orphans that is UNAUTHENTICATED by design. Event
                # consumers must treat it as a claim, not an identity.
                try:
                    log_event(
                        "message.orphan_delivered",
                        f"outbox orphan {msg_id} ({msg.from_} → {msg.to}: "
                        f"{msg.label}) late-delivered after {age}s — the "
                        f"sender-side copy had no inbox twin (bus bypass "
                        f"or partial write)",
                        agent=msg.from_,
                        meta={
                            "msg_id": msg_id,
                            "to": msg.to,
                            "label": msg.label,
                            "age_seconds": age,
                            "outbox_path": str(f),
                        },
                        paths=paths,
                    )
                except Exception:
                    pass
                # Known gap (accepted, on the ledger): unlike
                # send_message, late delivery does NOT escalate
                # high-priority labels to a session-respawn — an
                # orphaned !urgent to a dormant recipient gets only
                # this best-effort wake and otherwise waits for the
                # recipient's next inbox view.
                try:
                    wake_recipient_if_live(
                        msg.to, msg.label, msg.from_, msg.body, paths=paths,
                    )
                except Exception:
                    pass
                out.append(result)
            except Exception:
                # One bad file must not starve the rest of the sweep.
                continue
    return out


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


def scan_inbox_messages() -> list[Message]:
    """Return every message in any canonical ``.messages/inbox/``.

    Mirrors :func:`metasphere.consolidate.scan_active_tasks` — used by
    the lifecycle consolidator. Walks
    ``~/.metasphere/projects/*/.messages/inbox/`` plus the global
    bucket (see ``_canonical_inbox_dirs``).
    """
    out: list[Message] = []
    for inbox in _canonical_inbox_dirs():
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
    # Refresh the discovery index so ``msg read`` resolves the new
    # location without falling back to the slow archive walk. The index
    # is best-effort — failures don't break archival.
    try:
        msg_id = msg_path.stem
        _index_add(msg_id, dest, resolve())
    except Exception:
        pass
    return dest


def mark_read(msg_id: str, paths: Paths | None = None) -> Message:
    """Promote an UNREAD message to READ and stamp ``read_at``.

    Sacred labels (``!task``, ``!query``) are left unread: they require
    explicit action by the recipient, and stamping ``read_at`` from any
    curious peek-read would pollute the STALE window that
    :mod:`metasphere.consolidate` computes from that timestamp. Mirrors
    the guard in :func:`read_message` view-mode (see issue #109).

    Discovery walks inbox → archive → outbox via
    :func:`_find_message_anywhere` so messages remain readable across
    their full lifecycle. The ``read_at`` mutation only fires for live
    inbox copies — archived or outbox-only copies are returned
    read-only because:
    archived messages have already completed their lifecycle, and
    outbox copies are sender-owned (mutating them on a recipient read
    would invert ownership).
    """
    paths = paths or resolve()
    p = _find_message_anywhere(msg_id, paths=paths)
    if p is None:
        raise FileNotFoundError(f"message {msg_id} not found")
    with file_lock(_lock_path(p)):
        msg = read_message(p)
        if (msg.status == STATUS_UNREAD
                and msg.label not in SACRED_LABELS
                and p.parent.name == "inbox"):
            msg.status = STATUS_READ
            msg.read_at = _utcnow()
            write_frontmatter_file(p, msg.to_frontmatter())
        return msg


# ---------------------------------------------------------------------------
# Wake (tmux plumbing in metasphere.tmux)
# ---------------------------------------------------------------------------


def _is_wakeable_agent_target(target: str, paths: Paths) -> bool:
    """True iff ``target`` is the form ``@<agent_name>`` and resolves to a
    registered persistent agent (not a project, not a scope-relative
    pointer, not ``@user``).

    Used by :func:`send_message` to gate the high-priority escalation
    to :func:`metasphere.agents.wake_persistent`. Cheaper than letting
    ``wake_persistent`` raise ``ValueError`` for the common project /
    pointer cases, and lets us preserve the explicit-check preference
    from the dispatch brief.
    """
    if not target.startswith("@"):
        return False
    if target in ("@user", "@..", "@."):
        return False
    if target.startswith("@/"):
        return False
    name = target[1:]
    if not name:
        return False
    # If the name resolves to a registered project, it's not an agent —
    # projects don't have tmux sessions.
    try:
        from . import project as _project
        if _project.get_project(name, paths=paths) is not None:
            return False
    except Exception:
        # Defensive: any project-registry hiccup falls through to the
        # wake_persistent ValueError path, which is also caught.
        pass
    return True


def wake_recipient_if_live(
    target: str,
    label: str,
    from_agent: str,
    body: str,
    paths: Paths | None = None,
) -> bool:
    """Best-effort wake via :mod:`metasphere.tmux`.

    Returns True when the wake notice actually landed on the target's
    pane, False otherwise (no session, defer, submit failure, or
    unresolvable target). The bool was added for issue #106 — callers
    that previously treated wake-failure as success could leave
    scheduled tasks stranded in inboxes after a gateway cascade-restart
    killed the target session mid-fire.

    Exceptions are caught + logged at WARNING (not silently swallowed)
    so the failure shows up in the schedule/heartbeat daemon logs.
    """
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
        return False

    # Project-scoped agents have sessions named
    # ``metasphere-<project>-<agent>`` (see ``AgentRecord.session_name``),
    # which the bare ``metasphere-<name>`` constructor misses. Route
    # through ``_resolve_session`` so wakes targeting project-scoped
    # research / domain agents actually hit the right pane (issue #106).
    from .session import _resolve_session
    session = _resolve_session(f"@{agent_name}")
    body_preview = body[:200] + ("..." if len(body) > 200 else "")
    notice = f"[wake] new {label} from {from_agent}: {body_preview}"

    delivered = False
    try:
        # defer_if_busy=True: agent-to-agent wakes are auto-fired;
        # never interleave with a human typing into the target pane.
        # escape_prefix=False: wakes must never interrupt a tool call
        # running in the target pane; the wake text queues until the
        # tool finishes.
        delivered = bool(
            _tmux_submit(session, notice, defer_if_busy=True, escape_prefix=False)
        )
    except Exception as e:
        logger.warning(
            "wake_recipient_if_live(%s): tmux submit raised: %s", target, e,
        )

    try:
        log_event(
            "agent.wake",
            f"@{agent_name} woken by {from_agent} ({label})"
            + ("" if delivered else " [submit failed]"),
            agent=from_agent,
            paths=paths,
        )
    except Exception as e:
        logger.warning(
            "wake_recipient_if_live(%s): log_event raised: %s", target, e,
        )

    return delivered
