"""CAM-backed recall strategy.

Shells out to the ``cam`` CLI (external to metasphere). If the binary
is missing, ``search`` returns an empty list and logs a single warning
event per process.
"""

from __future__ import annotations

import json
import shutil
import subprocess

from ..events import log_event
from .base import MemoryHit, MemoryStrategy

_CAM_MISSING_WARNED = False


def _warn_missing_once() -> None:
    global _CAM_MISSING_WARNED
    if _CAM_MISSING_WARNED:
        return
    _CAM_MISSING_WARNED = True
    try:
        log_event("memory.cam.missing", "cam binary not found on PATH")
    except Exception:
        pass


class CamStrategy(MemoryStrategy):
    """Wraps ``cam search --json`` as a memory backend."""

    name = "cam"

    def __init__(self, binary: str = "cam", timeout: float = 5.0, fast: bool = True) -> None:
        self._binary = binary
        self._timeout = timeout
        self._fast = fast

    def search(self, query: str, limit: int = 5) -> list[MemoryHit]:
        if not query.strip():
            return []
        if shutil.which(self._binary) is None:
            _warn_missing_once()
            return []
        cmd = [self._binary, "search", query, "--limit", str(limit), "--json"]
        if self._fast:
            cmd.append("--fast")
        try:
            res = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self._timeout,
            )
        except (subprocess.SubprocessError, OSError):
            return []
        if res.returncode != 0 or not res.stdout.strip():
            return []
        try:
            raw = json.loads(res.stdout)
        except json.JSONDecodeError:
            return []
        if not isinstance(raw, list):
            return []

        # Normalize cam scores (often >1) to 0..1 by dividing by the
        # max score in the response.
        max_score = max((float(r.get("score", 0.0)) for r in raw if isinstance(r, dict)), default=0.0)
        if max_score <= 0:
            max_score = 1.0

        out: list[MemoryHit] = []
        for r in raw:
            if not isinstance(r, dict):
                continue
            raw_score = float(r.get("score", 0.0))
            out.append(
                MemoryHit(
                    source=r.get("path", "cam"),
                    score=raw_score / max_score,
                    excerpt=(r.get("snippet") or r.get("title") or "")[:200],
                    metadata={
                        "agent": r.get("agent"),
                        "machine": r.get("machine"),
                        "date": r.get("date"),
                        "raw_score": raw_score,
                        "strategy": "cam",
                    },
                )
            )
        return out[:limit]
