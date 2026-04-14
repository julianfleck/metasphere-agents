"""Atomic file I/O, locking, JSON, and YAML-frontmatter helpers.

Pure stdlib. The frontmatter parser is intentionally minimal: it
understands flat ``key: value`` blocks between ``---`` fences, plus
simple ``[a, b]`` inline lists. It is *not* a general YAML parser —
that would require a third-party dep, which is forbidden for now.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator


# ---------- atomic write ----------

def atomic_write_text(path: Path, data: str, *, mode: int = 0o644) -> None:
    """Write `data` to `path` atomically (tmp file + rename).

    Creates parent dirs as needed. Survives crashes mid-write.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def atomic_write_bytes(path: Path, data: bytes, *, mode: int = 0o644) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


# ---------- locking ----------

@contextlib.contextmanager
def file_lock(path: Path, *, exclusive: bool = True) -> Iterator[None]:
    """Acquire an advisory flock on `path` (created if missing).

    Used for any file that more than one process may mutate
    concurrently — events.jsonl, jobs.json, the message inboxes, etc.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    flag = fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o644)
    try:
        fcntl.flock(fd, flag)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


# ---------- json with locks ----------

def read_json(path: Path, default: Any = None) -> Any:
    path = Path(path)
    if not path.exists():
        return default
    with file_lock(path, exclusive=False):
        with open(path, "r", encoding="utf-8") as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return default


def write_json(path: Path, data: Any) -> None:
    path = Path(path)
    with file_lock(path):
        atomic_write_text(path, json.dumps(data, indent=2, sort_keys=True) + "\n")


def append_jsonl(path: Path, record: dict[str, Any]) -> None:
    """Atomically append a single JSON record + newline under flock."""
    path = Path(path)
    line = json.dumps(record, sort_keys=True) + "\n"
    with file_lock(path):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
            f.flush()
            os.fsync(f.fileno())


# ---------- frontmatter ----------

@dataclass
class Frontmatter:
    meta: dict[str, Any]
    body: str


def _parse_scalar(v: str) -> Any:
    v = v.strip()
    if v.startswith("[") and v.endswith("]"):
        inner = v[1:-1].strip()
        if not inner:
            return []
        return [_parse_scalar(p) for p in inner.split(",")]
    if (v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'")):
        return v[1:-1]
    if v.lower() in ("true", "false"):
        return v.lower() == "true"
    if v.lower() in ("null", "~", ""):
        return None
    try:
        return int(v)
    except ValueError:
        pass
    try:
        return float(v)
    except ValueError:
        pass
    return v


def parse_frontmatter(text: str) -> Frontmatter:
    """Parse a ``---``-fenced flat YAML frontmatter block + body."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return Frontmatter({}, text)
    meta: dict[str, Any] = {}
    i = 1
    while i < len(lines) and lines[i].strip() != "---":
        line = lines[i]
        if line.strip() and ":" in line:
            k, _, v = line.partition(":")
            meta[k.strip()] = _parse_scalar(v)
        i += 1
    body = "\n".join(lines[i + 1 :]) if i < len(lines) else ""
    return Frontmatter(meta, body)


def _format_scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if v is None:
        return "null"
    if isinstance(v, list):
        return "[" + ", ".join(_format_scalar(x) for x in v) + "]"
    if isinstance(v, (int, float)):
        return str(v)
    s = str(v)
    # ISO-8601 timestamps contain ':' but bash callers expect them
    # unquoted. Emit them bare so the on-disk frontmatter stays
    # byte-compatible with the legacy bash writers.
    if _ISO8601_RE.match(s):
        return s
    # YAML-significant leading sigils: bare `@agent` and `!label` parse as
    # tags/aliases under a strict YAML loader and break the render pipeline
    # downstream (the tolerant in-repo parser accepts them, but exporters
    # that feed PyYAML-based tooling do not). Quote them proactively.
    if s and s[0] in ("@", "!"):
        return json.dumps(s)
    if any(c in s for c in ":#\n") or s != s.strip():
        return json.dumps(s)
    return s


_ISO8601_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?$"
)


def serialize_frontmatter(fm: Frontmatter) -> str:
    if not fm.meta:
        return fm.body
    lines = ["---"]
    for k, v in fm.meta.items():
        lines.append(f"{k}: {_format_scalar(v)}")
    lines.append("---")
    head = "\n".join(lines)
    if not fm.body:
        return head + "\n"
    # Ensure exactly one newline separates the closing fence from body so
    # repeated round-trips don't accumulate blank lines (the parser drops
    # the fence-terminating newline but keeps everything after).
    body = fm.body if fm.body.startswith("\n") else "\n" + fm.body
    return head + body


def read_frontmatter_file(path: Path) -> Frontmatter:
    return parse_frontmatter(Path(path).read_text(encoding="utf-8"))


def write_frontmatter_file(path: Path, fm: Frontmatter) -> None:
    atomic_write_text(Path(path), serialize_frontmatter(fm))
