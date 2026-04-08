"""Agent lifecycle: spawn (ephemeral) and wake (persistent).

Python port of ``scripts/metasphere-spawn`` and ``scripts/metasphere-wake``.
This module owns identity-directory creation, the spawn-vs-wake split, and
the tmux/REPL bring-up sequence for persistent agents.

Why this shape:
- Ephemeral spawn = headless ``claude -p`` one-shot. No tmux session.
- Persistent wake = ``metasphere-<name>`` tmux session running a respawn
  loop around ``claude --dangerously-skip-permissions``. Persistence is
  declared by the presence of ``MISSION.md`` in the agent dir.

Tmux paste-submission stays in bash (``scripts/metasphere-tmux-submit``)
per PORTING invariant 15 — Python shells out to it. The same goes for
the respawn loop and readiness poll: this module just orchestrates the
tmux commands and the script call.
"""

from __future__ import annotations

import datetime as _dt
import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from .events import log_event
from .io import atomic_write_text, file_lock
from .paths import Paths, resolve

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SESSION_PREFIX = "metasphere-"
_READY_TIMEOUT_S = 15
_READY_MARKER = "bypass permissions"


def _utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _normalize_name(name: str) -> str:
    return name if name.startswith("@") else "@" + name


def _tmux_bin() -> str:
    return shutil.which("tmux") or "tmux"


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------

@dataclass
class AgentRecord:
    name: str  # always with @ prefix
    scope: str
    parent: str
    status: str
    spawned_at: str
    mission_path: Optional[Path] = None
    pid_file: Optional[Path] = None
    agent_dir: Optional[Path] = None

    @property
    def session_name(self) -> str:
        return session_name_for(self.name)

    @property
    def is_persistent(self) -> bool:
        return self.mission_path is not None and self.mission_path.is_file()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def session_name_for(agent_name: str) -> str:
    """Return the canonical tmux session name for ``agent_name``.

    Invariant 7: ``metasphere-<name>`` with the leading ``@`` stripped.
    """
    return _SESSION_PREFIX + _normalize_name(agent_name)[1:]


def session_alive(name: str) -> bool:
    """True iff a tmux session named ``name`` exists."""
    try:
        r = subprocess.run(
            [_tmux_bin(), "has-session", "-t", name],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
        )
        return r.returncode == 0
    except FileNotFoundError:
        return False


def _read_text(p: Path, default: str = "") -> str:
    try:
        return p.read_text(encoding="utf-8").strip()
    except (OSError, FileNotFoundError):
        return default


def _agent_record_from_dir(agent_dir: Path) -> AgentRecord:
    name = agent_dir.name  # already starts with @
    mission = agent_dir / "MISSION.md"
    pid_file = agent_dir / "pid"
    return AgentRecord(
        name=name,
        scope=_read_text(agent_dir / "scope"),
        parent=_read_text(agent_dir / "parent"),
        status=_read_text(agent_dir / "status"),
        spawned_at=_read_text(agent_dir / "spawned_at"),
        mission_path=mission if mission.is_file() else None,
        pid_file=pid_file if pid_file.is_file() else None,
        agent_dir=agent_dir,
    )


def list_agents(paths: Paths | None = None) -> list[AgentRecord]:
    """Enumerate ``~/.metasphere/agents/@*/`` as :class:`AgentRecord`s."""
    paths = paths or resolve()
    if not paths.agents.is_dir():
        return []
    out: list[AgentRecord] = []
    for entry in sorted(paths.agents.iterdir()):
        if not entry.is_dir() or not entry.name.startswith("@"):
            continue
        out.append(_agent_record_from_dir(entry))
    return out


def is_persistent(agent: AgentRecord) -> bool:
    return agent.is_persistent


# ---------------------------------------------------------------------------
# Harness rendering
# ---------------------------------------------------------------------------

def _render_harness(agent_id: str, scope_path: str, parent: str, task: str, timestamp: str) -> str:
    return f"""# Agent: {agent_id}

You are **{agent_id}**, an autonomous agent working in the Metasphere system.

## Your Context

| Field | Value |
|-------|-------|
| Agent ID | {agent_id} |
| Scope | `{scope_path}` |
| Parent | {parent} |
| Spawned | {timestamp} |

## Your Task

{task}

---

You are autonomous. Work through your task systematically, communicate
status via `messages send @.. !info`, ask for help if blocked, and
complete your objective. When done:

    echo "complete: summary" > ~/.metasphere/agents/{agent_id}/status
    messages send @.. !done "Completed: ..."
"""


# ---------------------------------------------------------------------------
# Spawn (ephemeral)
# ---------------------------------------------------------------------------

def _resolve_scope(scope_path: str, repo_root: Path) -> Path:
    if scope_path.startswith("/"):
        # Repo-relative absolute (the bash convention).
        s = repo_root / scope_path.lstrip("/")
    else:
        s = repo_root / scope_path
    return Path(str(s).rstrip("/"))


def _atomic_meta_write(agent_dir: Path, name: str, value: str) -> None:
    """Write a small metadata file under flock so concurrent spawn/wake
    calls cannot tear it."""
    target = agent_dir / name
    with file_lock(agent_dir / f".{name}.lock"):
        atomic_write_text(target, value if value.endswith("\n") else value + "\n")


def spawn_ephemeral(
    agent_name: str,
    scope_path: str,
    task: str,
    parent: str = "@orchestrator",
    paths: Paths | None = None,
) -> AgentRecord:
    """Create an ephemeral one-shot agent and (unless opted out) launch
    it headless via ``claude -p``.

    Mirrors ``scripts/metasphere-spawn``. Honors ``METASPHERE_SPAWN_NO_EXEC=1``.
    """
    paths = paths or resolve()
    agent_id = _normalize_name(agent_name)
    timestamp = _utcnow()

    scope_abs = _resolve_scope(scope_path, paths.repo)

    # Scope dirs (so messages/tasks have somewhere to land).
    (scope_abs / ".tasks" / "active").mkdir(parents=True, exist_ok=True)
    (scope_abs / ".tasks" / "completed").mkdir(parents=True, exist_ok=True)
    (scope_abs / ".messages" / "inbox").mkdir(parents=True, exist_ok=True)
    (scope_abs / ".messages" / "outbox").mkdir(parents=True, exist_ok=True)

    agent_dir = paths.agent_dir(agent_id)
    agent_dir.mkdir(parents=True, exist_ok=True)

    _atomic_meta_write(agent_dir, "task", task)
    _atomic_meta_write(agent_dir, "status", f"spawned: {task}")
    _atomic_meta_write(agent_dir, "scope", str(scope_abs))
    _atomic_meta_write(agent_dir, "parent", parent)
    _atomic_meta_write(agent_dir, "spawned_at", timestamp)

    # Create a backing .tasks/active/<slug>.md and link it to the agent
    # via agent_dir/task_id. This is what lets posthook auto-archive the
    # task on clean exit (see metasphere.posthook.auto_close_finished_task).
    # Without this linkage, every ephemeral agent leaks a stale "pending"
    # task on the backlog — which is exactly the systemic gap that the
    # 2026-04-08 backlog audit found (10/23 active tasks were already
    # done but never closed).
    try:
        from .tasks import create_task

        backing = create_task(
            title=task,
            priority="!normal",
            scope=scope_abs,
            repo_root=paths.repo,
            created_by=parent,
        )
        _atomic_meta_write(agent_dir, "task_id", backing.id)
    except Exception:  # noqa: BLE001 — task linkage is best-effort
        pass

    # Named-agent inbox (for direct addressing).
    (paths.root / "messages" / agent_id / "inbox").mkdir(parents=True, exist_ok=True)

    harness_path = agent_dir / "harness.md"
    harness = _render_harness(agent_id, scope_path, parent, task, timestamp)
    atomic_write_text(harness_path, harness)

    # Initial !task message in the agent's scope. Local import dodges
    # the messages → events → identity dependency cycle on cold start.
    try:
        from .messages import send_message

        # We send into the scope by addressing the scope path itself.
        send_message(
            target="@" + scope_path,
            label="!task",
            body=task,
            from_agent=parent,
            paths=paths,
            wake=False,
        )
    except Exception:
        pass

    record = AgentRecord(
        name=agent_id,
        scope=str(scope_abs),
        parent=parent,
        status=f"spawned: {task}",
        spawned_at=timestamp,
        mission_path=None,
        agent_dir=agent_dir,
    )

    no_exec = os.environ.get("METASPHERE_SPAWN_NO_EXEC", "0") == "1"
    if no_exec:
        log_event(
            "agent.spawn",
            f"{agent_id} spawned at {scope_path} (no-exec)",
            agent=parent,
            meta={"child": agent_id, "scope": scope_path, "no_exec": True},
            paths=paths,
        )
        return record

    if shutil.which("claude") is None:
        log_event(
            "agent.spawn",
            f"{agent_id} harness ready at {scope_path} (claude not in PATH)",
            agent=parent,
            meta={"child": agent_id, "scope": scope_path, "claude_missing": True},
            paths=paths,
        )
        return record

    log_file = agent_dir / "output.log"
    pid_file = agent_dir / "pid"

    env = os.environ.copy()
    env.update(
        {
            "METASPHERE_AGENT_ID": agent_id,
            "METASPHERE_SCOPE": str(scope_abs),
            "METASPHERE_REPO_ROOT": str(paths.repo),
            "METASPHERE_DIR": str(paths.root),
        }
    )

    cwd = scope_abs if scope_abs.is_dir() else paths.repo
    log_fh = None
    try:
        log_fh = open(log_file, "ab")
        proc = subprocess.Popen(
            [
                "claude",
                "-p",
                harness,
                "--dangerously-skip-permissions",
            ],
            stdin=subprocess.DEVNULL,
            stdout=log_fh,
            stderr=log_fh,
            cwd=str(cwd),
            env=env,
            start_new_session=True,
            close_fds=True,
        )
    finally:
        # Popen dups the fd; we can drop our handle so the parent isn't
        # holding the log file open. Guarded so an open() failure above
        # doesn't AttributeError here.
        if log_fh is not None:
            log_fh.close()

    atomic_write_text(pid_file, str(proc.pid) + "\n")
    record.pid_file = pid_file

    log_event(
        "agent.spawn",
        f"{agent_id} spawned at {scope_path} (pid {proc.pid})",
        agent=parent,
        meta={"child": agent_id, "scope": scope_path, "pid": proc.pid},
        paths=paths,
    )
    return record


# ---------------------------------------------------------------------------
# Wake (persistent)
# ---------------------------------------------------------------------------

def _tmux_run(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        [_tmux_bin(), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        check=check,
    )


def _capture_pane(session: str) -> str:
    r = _tmux_run("capture-pane", "-p", "-t", session)
    return r.stdout if r.returncode == 0 else ""


def _wait_for_ready(session: str, timeout_s: int = _READY_TIMEOUT_S) -> bool:
    for _ in range(timeout_s):
        if _READY_MARKER in _capture_pane(session):
            return True
        time.sleep(1)
    return False


def _submit_via_bash(session: str, body: str, paths: Paths) -> None:
    """Shell out to scripts/metasphere-tmux-submit (invariant 15)."""
    submit_script = paths.repo / "scripts" / "metasphere-tmux-submit"
    if not submit_script.is_file():
        return
    # The bash file defines submit_to_tmux as a function — source it then call.
    # shlex.quote both the script path and tmux binary so a path containing
    # whitespace or quotes can never break the bash -c source.
    cmd = (
        f"source {shlex.quote(str(submit_script))}; "
        f"TMUX_CMD={shlex.quote(_tmux_bin())} submit_to_tmux \"$1\" \"$2\""
    )
    subprocess.run(
        ["bash", "-c", cmd, "_", session, body],
        check=False,
    )


def wake_persistent(
    agent_name: str,
    first_task: Optional[str] = None,
    paths: Paths | None = None,
) -> AgentRecord:
    """Wake (or attach to) a persistent agent's tmux+REPL session.

    Mirrors ``scripts/metasphere-wake``. Invariant 16: if the session is
    already alive, only the optional task is injected — no new session.
    """
    paths = paths or resolve()
    agent_id = _normalize_name(agent_name)
    agent_dir = paths.agent_dir(agent_id)
    mission = agent_dir / "MISSION.md"
    if not mission.is_file():
        raise ValueError(
            f"{agent_id} is not a persistent agent (no MISSION.md at {mission})"
        )

    scope_str = _read_text(agent_dir / "scope") or str(paths.repo)
    session = session_name_for(agent_id)

    if session_alive(session):
        if first_task:
            _submit_via_bash(session, f"[task] {first_task}", paths)
        return _agent_record_from_dir(agent_dir)

    # Cold start.
    _tmux_run("new-session", "-d", "-s", session, "-c", scope_str, check=False)
    _tmux_run("set-option", "-t", session, "mouse", "on")
    _tmux_run("set-option", "-t", session, "history-limit", "100000")

    # shlex.quote each value so apostrophes in scope/path don't break the shell.
    env_export = (
        f"export METASPHERE_AGENT_ID={shlex.quote(agent_id)} "
        f"METASPHERE_SCOPE={shlex.quote(scope_str)} "
        f"METASPHERE_REPO_ROOT={shlex.quote(str(paths.repo))} "
        f"METASPHERE_DIR={shlex.quote(str(paths.root))}"
    )
    _tmux_run("send-keys", "-t", session, env_export, "Enter")

    respawn = (
        "exec bash -c 'while true; do claude --dangerously-skip-permissions; "
        'ec=$?; echo "[wake] claude exited ($ec), respawning in 1s..."; '
        "sleep 1; done'"
    )
    _tmux_run("send-keys", "-t", session, respawn, "Enter")

    _atomic_meta_write(agent_dir, "status", "active: persistent session")
    _atomic_meta_write(agent_dir, "spawned_at", _utcnow())

    log_event(
        "agent.session",
        f"{agent_id} session started via wake",
        agent=agent_id,
        paths=paths,
    )

    _wait_for_ready(session)
    # Clear stray buffer characters from the exec-bash transition.
    # NB: send-keys C-u (no Enter) — readline kill-line. Mirrors the bash
    # invariant in scripts/metasphere-wake:158; do not "fix" by adding Enter.
    _tmux_run("send-keys", "-t", session, "C-u")
    time.sleep(0.2)

    if first_task:
        _submit_via_bash(session, f"[task] {first_task}", paths)

    return _agent_record_from_dir(agent_dir)


# ---------------------------------------------------------------------------
# Dormant GC
# ---------------------------------------------------------------------------

def _session_idle_seconds(session: str) -> Optional[int]:
    r = _tmux_run("display-message", "-t", session, "-p", "#{session_activity}")
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        activity = int(r.stdout.strip())
    except ValueError:
        return None
    return max(0, int(time.time()) - activity)


def gc_dormant(paths: Paths | None = None, max_idle_seconds: int = 86400) -> list[str]:
    """Return persistent agent names whose tmux session has been idle
    longer than ``max_idle_seconds``. Does NOT kill — caller decides.
    """
    paths = paths or resolve()
    out: list[str] = []
    for agent in list_agents(paths):
        if not agent.is_persistent:
            continue
        session = agent.session_name
        if not session_alive(session):
            continue
        idle = _session_idle_seconds(session)
        if idle is not None and idle > max_idle_seconds:
            out.append(agent.name)
    return out
