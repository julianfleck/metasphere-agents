"""Agent liveness probe — is an agent *generating*, *idle*, *stale*, or *dead*?

The reusable ground-truth layer for "who is actually working on what, right
now", sourced from real tmux pane freshness rather than whether the work was
filed as a task object. Designed in
internal design notes (Part B) as the
single truth source three callers want:

1. ``/status`` — the human "who's working" view. (The output redesign — one
   ``/status`` vs. a split ``/active`` — is open question #2 in the proposal and
   is deliberately NOT wired here. This module ships the probe; wiring follows
   the product decision.)
2. The work-planner / dormant-projects sweep — a project with zero filed tasks
   but a ``generating`` agent is *active*; that is the precise fix for
   "dormant-but-active was invisible."
3. The dormancy reaper cross-check — a ``generating`` agent must never be reaped
   as idle.

Mechanism (cheap, version-independent): tail the last few lines of the agent's
tmux pane, hash them, and compare to the previous snapshot cached under
``$METASPHERE_DIR/state/liveness/<session>.json``. A changed hash means output
moved since the last capture → ``generating``; an unchanged hash that is older
than the stale threshold → ``stale``; in between → ``idle``. The Claude Code
TUI also renders ``esc to interrupt`` in its footer while a turn/tool is running
(confirmed live 2026-06-29 against v2.1.185 — it is composed at runtime, not a
bundle string literal), so its presence is a confirming secondary signal that
detects ``generating`` even within a single capture.

The probe never raises — any tmux/OS hiccup degrades to ``unknown`` (alive but
activity indeterminate) rather than guessing, matching the reaper's fail-open
posture. The first capture after a cold start has no prior snapshot and reports
``unknown``; the next heartbeat warms it.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from metasphere.agents import (
    AgentRecord,
    _capture_pane,
    list_agents,
    session_alive,
)
from metasphere.paths import Paths, resolve

# Liveness states.
GENERATING = "generating"
IDLE = "idle"
STALE = "stale"
DEAD = "dead"
UNKNOWN = "unknown"

#: States that mean the agent is doing real work this instant.
ACTIVE_STATES = frozenset({GENERATING})

# Defaults — all env-overridable (ship-with-defaults posture, tune from data,
# matching the memory-threshold and reaper work). The idle/stale boundary
# aligns conceptually with the existing reaper STALE ladder.
_TAIL_LINES_DEFAULT = 6
_STALE_AFTER_S_DEFAULT = 600  # unchanged > 10 min → possible hang

#: The Claude Code footer fragment shown while a turn/tool is running.
_GENERATING_INDICATOR = "esc to interrupt"

_SAFE_KEY_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def _resolve_int_env(name: str, default: int) -> int:
    """Read a non-negative int from ``os.environ[name]``, else ``default``.

    Mirrors :func:`metasphere.agents._resolve_stale_threshold_sec`: bad or
    negative values warn to stderr and fall back rather than raising.
    """
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        v = int(raw)
    except ValueError:
        print(
            f"metasphere: {name} expects an integer, got {raw!r}; "
            f"using default {default}",
            file=sys.stderr,
        )
        return default
    if v < 0:
        print(
            f"metasphere: {name} must be non-negative, got {v}; "
            f"using default {default}",
            file=sys.stderr,
        )
        return default
    return v


@dataclass
class Liveness:
    """The liveness verdict for one agent."""

    agent: str  # @-prefixed name
    project: str  # project name, or "" for global
    session: str  # tmux session name
    state: str  # one of GENERATING / IDLE / STALE / DEAD / UNKNOWN
    idle_age_s: Optional[int]  # seconds since last observed change; None if N/A
    doing: str  # short "what they're on", from the agent's status sidecar

    @property
    def is_working(self) -> bool:
        return self.state in ACTIVE_STATES

    @property
    def is_alive(self) -> bool:
        return self.state != DEAD


@dataclass
class _Snapshot:
    hash: str
    captured_at: int
    last_change: int

    def to_json(self) -> dict:
        return {
            "hash": self.hash,
            "captured_at": self.captured_at,
            "last_change": self.last_change,
        }

    @classmethod
    def from_json(cls, d: dict) -> Optional["_Snapshot"]:
        try:
            return cls(
                hash=str(d["hash"]),
                captured_at=int(d["captured_at"]),
                last_change=int(d["last_change"]),
            )
        except (KeyError, TypeError, ValueError):
            return None


# Default probe — capture the pane via the shared agents helper. Returns the
# pane text when the session is alive, or None when it is dead. Injectable so
# tests never need a real tmux.
def _default_prober(session: str) -> Optional[str]:
    if not session_alive(session):
        return None
    return _capture_pane(session)


def _tail(text: str, n: int) -> str:
    """Last ``n`` lines of ``text``, joined — the slice we hash/scan."""
    lines = text.splitlines()
    return "\n".join(lines[-n:]) if lines else ""


def _has_indicator(tail: str) -> bool:
    return _GENERATING_INDICATOR in tail.lower()


def _doing_from_status(status: str) -> str:
    """Distil the agent's ``status`` sidecar into a short "what they're on".

    Status strings follow ``<verb>: <detail>`` (e.g. ``working: OPS-1 PR``,
    ``spawned: scan X``, ``active: persistent session``). Surface the detail;
    fall back to the whole string. Terminal/parked verbs carry no useful
    "doing" payload, so collapse them to empty.
    """
    status = (status or "").strip()
    if not status:
        return ""
    verb, _, detail = status.partition(":")
    verb = verb.strip().lower()
    detail = detail.strip()
    if verb in {"active", "dormant", "complete", "completed", "crashed", "abandoned"}:
        # Generic lifecycle noise, not a task description.
        return "" if not detail or verb != "complete" else detail
    return detail or status


def _snapshot_path(paths: Paths, session: str) -> Path:
    key = _SAFE_KEY_RE.sub("_", session)
    return paths.state / "liveness" / f"{key}.json"


def _load_snapshot(path: Path) -> Optional[_Snapshot]:
    try:
        return _Snapshot.from_json(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return None


def _save_snapshot(path: Path, snap: _Snapshot) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(snap.to_json()), encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass  # best-effort cache; a missed write just re-cold-starts next tick


def agent_liveness(
    agent: AgentRecord,
    *,
    paths: Optional[Paths] = None,
    now: Optional[int] = None,
    tail_lines: Optional[int] = None,
    stale_after_s: Optional[int] = None,
    prober: Optional[Callable[[str], Optional[str]]] = None,
    persist: bool = True,
) -> Liveness:
    """Return the :class:`Liveness` verdict for one ``agent``.

    Pure except for the snapshot cache write (suppressed with
    ``persist=False``). ``now`` (epoch seconds) and ``prober`` are injectable
    for tests — ``prober(session)`` returns the pane text, or ``None`` for a
    dead session. Never raises.
    """
    paths = paths or resolve()
    now = int(time.time()) if now is None else int(now)
    tail_lines = (
        _resolve_int_env("METASPHERE_LIVENESS_TAIL_LINES", _TAIL_LINES_DEFAULT)
        if tail_lines is None
        else tail_lines
    )
    stale_after_s = (
        _resolve_int_env("METASPHERE_LIVENESS_STALE_AFTER_S", _STALE_AFTER_S_DEFAULT)
        if stale_after_s is None
        else stale_after_s
    )
    prober = prober or _default_prober

    session = agent.session_name
    doing = _doing_from_status(agent.status)

    def _verdict(state: str, idle_age: Optional[int]) -> Liveness:
        return Liveness(
            agent=agent.name,
            project=agent.project,
            session=session,
            state=state,
            idle_age_s=idle_age,
            doing=doing,
        )

    try:
        pane = prober(session)
    except Exception:  # noqa: BLE001 — probe must never raise into a caller
        return _verdict(UNKNOWN, None)

    if pane is None:
        return _verdict(DEAD, None)

    tail = _tail(pane, tail_lines)
    indicator = _has_indicator(tail)
    digest = hashlib.sha1(tail.encode("utf-8", "replace")).hexdigest()

    snap_path = _snapshot_path(paths, session)
    prior = _load_snapshot(snap_path)

    if prior is None:
        # Cold: no baseline to diff against. The indicator alone still proves
        # generation; otherwise we honestly report unknown until the next tick.
        if persist:
            _save_snapshot(snap_path, _Snapshot(digest, now, now))
        return _verdict(GENERATING if indicator else UNKNOWN, 0 if indicator else None)

    changed = digest != prior.hash
    last_change = now if changed else prior.last_change
    if persist:
        _save_snapshot(snap_path, _Snapshot(digest, now, last_change))

    idle_age = max(0, now - last_change)
    if indicator or changed:
        state = GENERATING
    elif idle_age <= stale_after_s:
        state = IDLE
    else:
        state = STALE
    return _verdict(state, idle_age)


def liveness_snapshot(
    paths: Optional[Paths] = None,
    *,
    now: Optional[int] = None,
    tail_lines: Optional[int] = None,
    stale_after_s: Optional[int] = None,
    prober: Optional[Callable[[str], Optional[str]]] = None,
    persist: bool = True,
    persistent_only: bool = True,
    include_dead: bool = False,
) -> list[Liveness]:
    """Probe every registered agent and return their verdicts.

    ``persistent_only`` (default) restricts to the long-lived team agents that
    the "who's working" view cares about. Dead agents are dropped unless
    ``include_dead``. Sorted by ``(project, agent)`` for a stable grouped view.
    """
    paths = paths or resolve()
    agents = list_agents(paths)
    if persistent_only:
        agents = [a for a in agents if a.is_persistent]

    out: list[Liveness] = []
    for agent in agents:
        lv = agent_liveness(
            agent,
            paths=paths,
            now=now,
            tail_lines=tail_lines,
            stale_after_s=stale_after_s,
            prober=prober,
            persist=persist,
        )
        if lv.state == DEAD and not include_dead:
            continue
        out.append(lv)

    out.sort(key=lambda lv: (lv.project, lv.agent))
    return out


def active_projects(
    paths: Optional[Paths] = None,
    *,
    now: Optional[int] = None,
    prober: Optional[Callable[[str], Optional[str]]] = None,
    persist: bool = True,
) -> set[str]:
    """Projects with ≥1 agent generating output right now.

    A task-file-independent activity oracle: most real work is dispatched
    ``!task`` messages + live sessions with **zero** filed tasks, so a
    task-file counter structurally under-reports who is working. A project
    with no filed tasks but a ``generating`` agent IS active. The
    dormant-projects sweep should treat this as a union with its filed-task
    set when deciding "dormant" (union semantics owned by @orchestrator).

    Pure read (the underlying probe persists its snapshot cache; pass
    ``persist=False`` to suppress). Never raises — degrades with the probe.
    The empty project name (global agents) is excluded.
    """
    return {
        lv.project
        for lv in liveness_snapshot(paths, now=now, prober=prober, persist=persist)
        if lv.is_working and lv.project
    }


# Marker glyphs for the grouped text view. Plain ASCII fallback is implicit in
# the meaning; the glyphs match the proposal's mockup.
_MARKERS = {
    GENERATING: "●",
    IDLE: "○",
    STALE: "◐",
    DEAD: "·",
    UNKNOWN: "·",
}


def _fmt_age(seconds: Optional[int]) -> str:
    if seconds is None:
        return ""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    return f"{seconds // 3600}h{(seconds % 3600) // 60:02d}m"


def format_liveness(items: list[Liveness]) -> str:
    """Render verdicts as a project-grouped "who's actually working" block.

    Mirrors the proposal mockup::

        widget
          ● @widget-eng  generating · Phase-1 cutover   Δ1s
          ○ @widget-lead idle 4m
    """
    if not items:
        return "(no agents)"

    by_project: dict[str, list[Liveness]] = {}
    for lv in items:
        by_project.setdefault(lv.project or "(global)", []).append(lv)

    name_w = max((len(lv.agent) for lv in items), default=0)
    lines: list[str] = []
    for project in sorted(by_project):
        lines.append(project)
        for lv in by_project[project]:
            marker = _MARKERS.get(lv.state, "·")
            age = _fmt_age(lv.idle_age_s)
            if lv.state == GENERATING:
                tail = f"generating{f' · {lv.doing}' if lv.doing else ''}"
                if age:
                    tail += f"   Δ{age}"
            elif lv.state in (IDLE, STALE):
                tail = lv.state + (f" {age}" if age else "")
            else:
                tail = lv.state
            lines.append(f"  {marker} {lv.agent.ljust(name_w)}  {tail}".rstrip())
    return "\n".join(lines)
