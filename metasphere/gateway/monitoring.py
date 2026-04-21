"""Host-health counters for the gateway and per-turn ALERT surfacing.

Three counter families, each a pure-Python probe with no side effects:

- ``zombie_counters()``     — total procfs state='Z' + ``npm root -g`` slice
- ``tmux_counters(paths)``  — live tmux sessions split by persistent /
                              ephemeral (via ``MISSION.md`` presence in
                              the matching agent directory)
- ``pid_headroom()``        — configured PID limit (cgroup pids.max
                              authoritative when finite, otherwise
                              ``/proc/sys/kernel/pid_max``), current
                              process count, and free-slot percentage.

Thresholds are expressed as a small dataclass so tests can inject
synthetic counters and rebuild the ALERT string deterministically. The
intent is defensive: when nothing is tripped, the ALERT renderer emits
an empty string and the per-turn context block is byte-identical to
the pre-monitoring shape.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from ..paths import Paths

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

ZOMBIE_THRESHOLD = 20
TMUX_THRESHOLD = 10
PID_HEADROOM_PCT_THRESHOLD = 20  # alert when free-slots pct drops below

# Test hook: when set to a non-empty string of the form
# ``zombies=N,tmux=M,pid_pct=P`` the ALERT renderer uses those numbers
# in place of live probes. Keeps the reproducible demo path out of the
# hot loop while still exercising the real composition logic.
_ENV_OVERRIDE = "METASPHERE_MONITORING_OVERRIDE"


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ZombieCounters:
    total: int
    npm_root_g: int


@dataclass(frozen=True)
class TmuxCounters:
    total: int
    persistent: int
    ephemeral: int


@dataclass(frozen=True)
class PidHeadroom:
    limit: int              # effective PID limit (0 when unlimited / unknown)
    current: int            # live process count
    free_pct: float         # percent of slots available (100.0 when unlimited)
    source: str             # 'cgroup' | 'kernel' | 'unknown'


@dataclass(frozen=True)
class MonitoringSnapshot:
    zombies: ZombieCounters
    tmux: TmuxCounters
    pids: PidHeadroom


# ---------------------------------------------------------------------------
# Zombies
# ---------------------------------------------------------------------------

_CMDLINE_NPM_ROOT_G = ("npm", "root", "-g")


def _iter_proc_dirs() -> list[Path]:
    try:
        return [
            Path("/proc") / entry
            for entry in os.listdir("/proc")
            if entry.isdigit()
        ]
    except OSError:
        return []


def _read_proc_status_state(pid_dir: Path) -> str:
    try:
        with open(pid_dir / "status", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("State:"):
                    # "State:\tZ (zombie)" -> "Z"
                    parts = line.split()
                    if len(parts) >= 2:
                        return parts[1]
                    break
    except OSError:
        pass
    return ""


def _read_proc_cmdline(pid_dir: Path) -> tuple[str, ...]:
    try:
        data = (pid_dir / "cmdline").read_bytes()
    except OSError:
        return ()
    if not data:
        return ()
    return tuple(p.decode("utf-8", errors="replace") for p in data.split(b"\x00") if p)


def zombie_counters() -> ZombieCounters:
    """Return (total zombies, npm-root-g zombies) by walking /proc."""
    total = 0
    npm = 0
    for pid_dir in _iter_proc_dirs():
        state = _read_proc_status_state(pid_dir)
        if state != "Z":
            continue
        total += 1
        cmdline = _read_proc_cmdline(pid_dir)
        # Zombies frequently have empty cmdline (process already reaped
        # its argv) — fall back to /proc/<pid>/comm for the basename.
        name_ok = cmdline[:3] == _CMDLINE_NPM_ROOT_G
        if not name_ok:
            try:
                comm = (pid_dir / "comm").read_text(encoding="utf-8").strip()
            except OSError:
                comm = ""
            name_ok = comm == "npm"
        if name_ok:
            npm += 1
    return ZombieCounters(total=total, npm_root_g=npm)


# ---------------------------------------------------------------------------
# Tmux
# ---------------------------------------------------------------------------

_AGENT_SESSION_PREFIX = "metasphere-"


def _tmux_list_sessions() -> list[str]:
    """Return tmux session names as reported by ``tmux list-sessions``.

    Empty list when tmux is not installed or no server is running.
    """
    try:
        r = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
        )
    except FileNotFoundError:
        return []
    if r.returncode != 0:
        return []
    return [line.strip() for line in r.stdout.splitlines() if line.strip()]


def _session_is_persistent(session: str, paths: Paths) -> bool:
    """True iff the session corresponds to an agent with MISSION.md.

    Session convention: ``metasphere-<name>`` for global agents and
    ``metasphere-<project>-<name>`` for project-scoped ones. Both map
    back to an agent directory that has ``MISSION.md`` iff persistent.
    The orchestrator session (``metasphere-orchestrator``) always
    counts as persistent — it IS the persistent collaborator.
    """
    if not session.startswith(_AGENT_SESSION_PREFIX):
        return False
    stripped = session[len(_AGENT_SESSION_PREFIX):]
    if not stripped:
        return False
    # Global agent: metasphere-<name> -> @<name>
    global_dir = paths.agents / f"@{stripped}"
    if (global_dir / "MISSION.md").is_file():
        return True
    # Project-scoped: metasphere-<project>-<name>; walk right-to-left so
    # agent names containing a hyphen still resolve when the project
    # name is a known directory.
    projects_dir = paths.projects
    if projects_dir.is_dir():
        parts = stripped.split("-")
        for split_at in range(1, len(parts)):
            project = "-".join(parts[:split_at])
            agent = "-".join(parts[split_at:])
            if not project or not agent:
                continue
            pdir = projects_dir / project / "agents" / f"@{agent}"
            if (pdir / "MISSION.md").is_file():
                return True
    return False


def tmux_counters(paths: Paths) -> TmuxCounters:
    sessions = _tmux_list_sessions()
    total = len(sessions)
    persistent = sum(1 for s in sessions if _session_is_persistent(s, paths))
    ephemeral = total - persistent
    return TmuxCounters(total=total, persistent=persistent, ephemeral=ephemeral)


# ---------------------------------------------------------------------------
# PID headroom
# ---------------------------------------------------------------------------

_PID_MAX_PATH = Path("/proc/sys/kernel/pid_max")
_PID_CGROUP_V2 = Path("/sys/fs/cgroup/pids.max")
_PID_CGROUP_UNIFIED = Path("/sys/fs/cgroup/unified/pids.max")


def _read_int(path: Path) -> int:
    try:
        raw = path.read_text(encoding="utf-8").strip()
    except OSError:
        return 0
    if not raw or raw == "max":
        return 0
    try:
        return int(raw)
    except ValueError:
        return 0


def _current_proc_count() -> int:
    return len(_iter_proc_dirs())


def pid_headroom() -> PidHeadroom:
    """Best-effort PID headroom probe.

    Cgroup pids.max wins when it is a finite number — on a pid-namespaced
    container (systemd-nspawn / Docker / k8s) that's the real ceiling.
    When the cgroup file is missing or ``max`` we fall back to
    /proc/sys/kernel/pid_max, which is the kernel-wide ceiling.
    """
    limit = 0
    source = "unknown"
    for candidate in (_PID_CGROUP_V2, _PID_CGROUP_UNIFIED):
        val = _read_int(candidate)
        if val > 0:
            limit = val
            source = "cgroup"
            break
    if limit == 0:
        kernel = _read_int(_PID_MAX_PATH)
        if kernel > 0:
            limit = kernel
            source = "kernel"
    current = _current_proc_count()
    if limit <= 0:
        return PidHeadroom(limit=0, current=current, free_pct=100.0, source=source)
    free = max(0, limit - current)
    free_pct = (free / limit) * 100.0
    return PidHeadroom(limit=limit, current=current, free_pct=free_pct, source=source)


# ---------------------------------------------------------------------------
# Composition
# ---------------------------------------------------------------------------

def snapshot(paths: Paths) -> MonitoringSnapshot:
    return MonitoringSnapshot(
        zombies=zombie_counters(),
        tmux=tmux_counters(paths),
        pids=pid_headroom(),
    )


def render_status(paths: Paths) -> str:
    """Human-facing block appended to ``metasphere gateway status``.

    Kept as a single string so the call site can simply ``print`` it.
    """
    snap = snapshot(paths)
    z = snap.zombies
    t = snap.tmux
    p = snap.pids
    limit_label = str(p.limit) if p.limit else "unlimited"
    lines = [
        f"zombies total={z.total} npm_root_g={z.npm_root_g}",
        f"tmux total={t.total} persistent={t.persistent} ephemeral={t.ephemeral}",
        (
            f"pid_headroom limit={limit_label} current={p.current} "
            f"free_pct={p.free_pct:.1f} source={p.source}"
        ),
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# ALERT renderer
# ---------------------------------------------------------------------------

_OVERRIDE_RE = re.compile(
    r"zombies=(?P<z>\d+),tmux=(?P<t>\d+),pid_pct=(?P<p>[\d.]+)"
)


def _parse_override(raw: str) -> MonitoringSnapshot | None:
    m = _OVERRIDE_RE.fullmatch(raw.strip())
    if not m:
        return None
    try:
        z = int(m.group("z"))
        t = int(m.group("t"))
        p = float(m.group("p"))
    except ValueError:
        return None
    return MonitoringSnapshot(
        zombies=ZombieCounters(total=z, npm_root_g=z),
        tmux=TmuxCounters(total=t, persistent=0, ephemeral=t),
        pids=PidHeadroom(limit=100, current=max(0, int(100 - p)),
                         free_pct=p, source="override"),
    )


def evaluate_alert(snap: MonitoringSnapshot) -> str:
    """Return a single-line ALERT string when any threshold trips, else ''.

    Conditions:
    - zombies.total > ZOMBIE_THRESHOLD
    - tmux.total > TMUX_THRESHOLD
    - pids.free_pct < PID_HEADROOM_PCT_THRESHOLD
    """
    trips: list[str] = []
    if snap.zombies.total > ZOMBIE_THRESHOLD:
        trips.append(
            f"zombies={snap.zombies.total} "
            f"(npm_root_g={snap.zombies.npm_root_g}) > {ZOMBIE_THRESHOLD}"
        )
    if snap.tmux.total > TMUX_THRESHOLD:
        trips.append(
            f"tmux_sessions={snap.tmux.total} "
            f"(persistent={snap.tmux.persistent}, ephemeral={snap.tmux.ephemeral}) "
            f"> {TMUX_THRESHOLD}"
        )
    if snap.pids.free_pct < PID_HEADROOM_PCT_THRESHOLD:
        trips.append(
            f"pid_headroom={snap.pids.free_pct:.1f}% "
            f"(current={snap.pids.current}/{snap.pids.limit or 'unlimited'}) "
            f"< {PID_HEADROOM_PCT_THRESHOLD}%"
        )
    if not trips:
        return ""
    return "## ALERT: " + "; ".join(trips) + "\n"


def render_alert(paths: Paths) -> str:
    """Probe live counters (or consume the env override) and return the
    ALERT block. Empty string when no threshold trips.
    """
    raw = os.environ.get(_ENV_OVERRIDE, "").strip()
    snap: MonitoringSnapshot | None = None
    if raw:
        snap = _parse_override(raw)
    if snap is None:
        try:
            snap = snapshot(paths)
        except Exception:
            # Monitoring must never break the turn. Fail closed to "no
            # alert" rather than letting a probe error blow up context
            # assembly.
            return ""
    return evaluate_alert(snap)
