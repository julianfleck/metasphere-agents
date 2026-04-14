"""Download Telegram message attachments so the orchestrator can see them.

Pure-text updates flow through the poller unchanged. When a message
carries any media payload (photo, document, audio, video, voice,
video_note, animation, sticker, or anything else the Bot API surfaces
as an object with a ``file_id``) we:

1. Parse the attachment metadata out of the raw Telegram payload.
2. Call ``getFile`` to resolve the server-side ``file_path``.
3. Download the bytes to
   ``~/.metasphere/attachments/<telegram_message_id>/<name>``.
4. Render an ``[attachments]`` block listing every downloaded path, so
   the orchestrator can hand them to Read / image tools itself.

Download failures never crash the poller — they become a note in the
rendered block so the LLM at least sees that the user sent something
we couldn't fetch.

Type-agnostic by design: we do not filter by MIME type. Claude decides
what to do with each file based on its extension / contents.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, List, Optional

from . import api

ATTACHMENTS_ROOT = Path.home() / ".metasphere" / "attachments"

#: ``photo`` arrives as a size-ascending array of thumbnails; every other
#: media key arrives as a single dict with a ``file_id``. Keeping this in
#: a set lets the generic scanner below skip the photo key cleanly.
_PHOTO_KEY = "photo"

_UNSAFE_FILENAME = re.compile(r"[^A-Za-z0-9._-]+")


@dataclass
class AttachmentRef:
    """Everything we need to call getFile + render a context-block line."""
    kind: str
    file_id: str
    file_size: Optional[int] = None
    file_name: Optional[str] = None
    mime_type: Optional[str] = None


@dataclass
class DownloadedAttachment:
    kind: str
    path: Optional[Path]
    file_size: Optional[int]
    mime_type: Optional[str]
    error: Optional[str] = None


def parse_attachments(msg: dict) -> List[AttachmentRef]:
    """Extract every downloadable attachment from a Telegram message dict.

    Strategy: handle ``photo`` explicitly (pick the largest thumbnail),
    then scan all other top-level keys for dicts carrying a ``file_id``.
    This intentionally catches ``animation``, ``sticker``, ``video_note``,
    and any future Bot API media type without a code change.
    """
    refs: List[AttachmentRef] = []

    photos = msg.get(_PHOTO_KEY) or []
    if isinstance(photos, list) and photos:
        biggest = max(photos, key=lambda p: p.get("file_size") or 0)
        if biggest.get("file_id"):
            refs.append(AttachmentRef(
                kind=_PHOTO_KEY,
                file_id=biggest["file_id"],
                file_size=biggest.get("file_size"),
                file_name=None,
                # Telegram re-encodes photos to JPEG server-side; recording
                # that here lets the renderer tag the line as "jpeg" even
                # when the getFile response doesn't echo a MIME type.
                mime_type="image/jpeg",
            ))

    for key, val in msg.items():
        if key == _PHOTO_KEY:
            continue
        if isinstance(val, dict) and val.get("file_id"):
            refs.append(AttachmentRef(
                kind=key,
                file_id=val["file_id"],
                file_size=val.get("file_size"),
                file_name=val.get("file_name"),
                mime_type=val.get("mime_type"),
            ))

    return refs


def _safe_filename(name: str) -> str:
    """Slug a user-supplied filename to ``[A-Za-z0-9._-]``.

    Telegram document names are attacker-controlled and can contain
    slashes / NUL / newlines. Anything outside the safe set becomes an
    underscore; leading dots/underscores are trimmed so a malicious
    ``.../passwd`` can't become ``.passwd``.
    """
    safe = _UNSAFE_FILENAME.sub("_", name).strip("._")
    return safe or "file"


def _default_filename(ref: AttachmentRef, file_path_hint: str) -> str:
    if ref.file_name:
        return _safe_filename(ref.file_name)
    # Telegram's getFile returns a ``file_path`` like ``photos/file_42.jpg``
    # — use its basename so we preserve the server-chosen extension.
    base = file_path_hint.rsplit("/", 1)[-1] if file_path_hint else ""
    if base:
        return _safe_filename(base)
    return f"{ref.kind}.bin"


# Injection seam for tests — override to skip the real HTTP call.
_HttpGet = Callable[[str, float], bytes]


def _http_get_default(url: str, timeout: float = 30.0) -> bytes:
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.read()


def download_attachment(
    ref: AttachmentRef,
    dest_dir: Path,
    *,
    http_get: Optional[_HttpGet] = None,
    call_fn: Optional[Callable[..., dict]] = None,
) -> DownloadedAttachment:
    """Download a single attachment. Never raises.

    On any failure (``TelegramAPIError`` from getFile, network error,
    filesystem error) we return a ``DownloadedAttachment`` with
    ``path=None`` and ``error`` set, so the caller can still render a
    note into the context block.
    """
    call_fn = call_fn or api.call
    http_get = http_get or _http_get_default
    try:
        resp = call_fn("getFile", file_id=ref.file_id)
        file_path = (resp.get("result") or {}).get("file_path") or ""
        if not file_path:
            return DownloadedAttachment(
                kind=ref.kind, path=None,
                file_size=ref.file_size, mime_type=ref.mime_type,
                error="getFile: no file_path in response",
            )
        # ``api._config()`` is the single source of truth for the bot
        # token; reusing it keeps the file URL in sync with whatever
        # token the rest of the process is talking to.
        cfg = api._config()
        url = f"https://api.telegram.org/file/bot{cfg.token}/{file_path}"
        data = http_get(url, 30.0)
        dest_dir.mkdir(parents=True, exist_ok=True)
        name = _default_filename(ref, file_path)
        dest = dest_dir / name
        dest.write_bytes(data)
        return DownloadedAttachment(
            kind=ref.kind, path=dest,
            file_size=ref.file_size or len(data),
            mime_type=ref.mime_type,
        )
    except api.TelegramAPIError as e:
        return DownloadedAttachment(
            kind=ref.kind, path=None,
            file_size=ref.file_size, mime_type=ref.mime_type,
            error=f"getFile: {e.description}",
        )
    except (OSError, urllib.error.URLError, ValueError) as e:
        return DownloadedAttachment(
            kind=ref.kind, path=None,
            file_size=ref.file_size, mime_type=ref.mime_type,
            error=f"download: {e}",
        )


def download_attachments(
    message_id: int,
    refs: List[AttachmentRef],
    root: Optional[Path] = None,
    *,
    http_get: Optional[_HttpGet] = None,
    call_fn: Optional[Callable[..., dict]] = None,
) -> List[DownloadedAttachment]:
    if not refs:
        return []
    # Resolve the root lazily so monkeypatching ``ATTACHMENTS_ROOT`` on
    # the module (e.g. in tests) is honored by callers that don't pass
    # ``root`` explicitly. A default-parameter binding would freeze the
    # value at function-definition time.
    if root is None:
        root = ATTACHMENTS_ROOT
    dest_dir = root / str(message_id)
    return [
        download_attachment(r, dest_dir, http_get=http_get, call_fn=call_fn)
        for r in refs
    ]


def _fmt_size(n: Optional[int]) -> str:
    if n is None:
        return "?"
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{int(size)} B" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def render_attachment_block(items: List[DownloadedAttachment]) -> str:
    """Render a human+LLM readable block of attachment lines.

    Format::

        [attachments]
        - photo: /home/.../12345/image.jpg (1.2 MB, jpeg)
        - document: /home/.../12345/report.pdf (345.0 KB, pdf)
        - audio: (download failed: getFile: FILE_NOT_FOUND)

    Returns empty string if ``items`` is empty, so callers can safely
    concatenate without worrying about stray blank blocks.
    """
    if not items:
        return ""
    lines = ["[attachments]"]
    for it in items:
        if it.error:
            lines.append(f"- {it.kind}: (download failed: {it.error})")
            continue
        size = _fmt_size(it.file_size)
        extras: List[str] = []
        if it.mime_type:
            ext = it.mime_type.rsplit("/", 1)[-1]
            if ext:
                extras.append(ext)
        tail = f", {', '.join(extras)}" if extras else ""
        lines.append(f"- {it.kind}: {it.path} ({size}{tail})")
    return "\n".join(lines)
