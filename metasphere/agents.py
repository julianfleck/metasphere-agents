"""Agent lifecycle: spawn (ephemeral) and wake (persistent).

This module owns identity-directory creation, the spawn-vs-wake split, and
the tmux/REPL bring-up sequence for persistent agents.

Why this shape:
- Ephemeral spawn = headless ``claude -p`` one-shot. No tmux session.
- Persistent wake = ``metasphere-<name>`` tmux session running a respawn
  loop around ``claude --dangerously-skip-permissions``. Persistence is
  declared by the presence of ``MISSION.md`` in the agent dir.

Tmux paste-submission uses :mod:`metasphere.tmux`. The respawn loop and
readiness poll stay as direct tmux commands: this module just orchestrates
the tmux commands and the submit call.
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
from .tmux import submit_to_tmux as _tmux_submit

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
    project: str = ""  # project name if project-scoped, empty if global

    @property
    def session_name(self) -> str:
        # Project-scoped agents include project in session name to avoid collisions
        if self.project:
            return _SESSION_PREFIX + self.project + "-" + _normalize_name(self.name)[1:]
        return session_name_for(self.name)

    @property
    def is_persistent(self) -> bool:
        return self.mission_path is not None and self.mission_path.is_file()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def session_name_for(agent_name: str) -> str:
    """Return the canonical tmux session name for ``agent_name``.

    Convention: ``metasphere-<name>`` with the leading ``@`` stripped.
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


def _agent_record_from_dir(agent_dir: Path, project: str = "") -> AgentRecord:
    name = agent_dir.name  # already starts with @
    mission = agent_dir / "MISSION.md"
    pid_file = agent_dir / "pid"
    # Read project pointer if not provided
    if not project:
        project = _read_text(agent_dir / "project")
    return AgentRecord(
        name=name,
        scope=_read_text(agent_dir / "scope"),
        parent=_read_text(agent_dir / "parent"),
        status=_read_text(agent_dir / "status"),
        spawned_at=_read_text(agent_dir / "spawned_at"),
        mission_path=mission if mission.is_file() else None,
        pid_file=pid_file if pid_file.is_file() else None,
        agent_dir=agent_dir,
        project=project,
    )


def _list_agents_in_dir(agents_dir: Path, project: str = "") -> list[AgentRecord]:
    """List agents from a single agents/ directory."""
    if not agents_dir.is_dir():
        return []
    out: list[AgentRecord] = []
    for entry in sorted(agents_dir.iterdir()):
        if not entry.is_dir() or not entry.name.startswith("@"):
            continue
        out.append(_agent_record_from_dir(entry, project=project))
    return out


def list_agents(paths: Paths | None = None, project: str = "") -> list[AgentRecord]:
    """Enumerate agents. Walks global + all project agent dirs.

    If ``project`` is specified, returns only agents for that project.
    """
    paths = paths or resolve()
    out: list[AgentRecord] = []

    if project:
        # Only look in the specified project
        project_agents = paths.project_agents_dir(project)
        out.extend(_list_agents_in_dir(project_agents, project=project))
    else:
        # Global agents
        out.extend(_list_agents_in_dir(paths.agents))
        # Walk all project agent dirs
        if paths.projects.is_dir():
            for proj_dir in sorted(paths.projects.iterdir()):
                if not proj_dir.is_dir():
                    continue
                proj_agents = proj_dir / "agents"
                if proj_agents.is_dir():
                    proj_name = proj_dir.name
                    out.extend(_list_agents_in_dir(proj_agents, project=proj_name))
    return out


def is_persistent(agent: AgentRecord) -> bool:
    return agent.is_persistent


# ---------------------------------------------------------------------------
# Harness rendering
# ---------------------------------------------------------------------------

def _render_harness(
    agent_id: str,
    scope_path: str,
    parent: str,
    task: str,
    timestamp: str,
    *,
    authority: str = "",
    responsibility: str = "",
    accountability: str = "",
) -> str:
    # Contract-first delegation (see agent-economy/NOTES-DEEPMIND-DELEGATION.md
    # for the mapping to DeepMind's Intelligent Delegation paper). If any
    # of the three fields are empty we still render a harness — legacy
    # spawns keep working — but the orchestrator is strongly nudged to
    # fill them in and the CLI/library layer warns on empty.
    contract = ""
    if authority or responsibility or accountability:
        contract = (
            "## Delegation Contract\n\n"
            "You were spawned under an explicit contract. Read it before\n"
            "you begin. If any field is ambiguous, do NOT guess — send\n"
            "`messages send @.. !query \"clarify: …\"` and wait.\n\n"
            f"### Authority (what you MAY do)\n\n{authority or '(unspecified — ask parent before acting)'}\n\n"
            f"### Responsibility (what you MUST produce)\n\n{responsibility or '(unspecified — ask parent)'}\n\n"
            f"### Accountability (how parent will verify)\n\n{accountability or '(unspecified — ask parent)'}\n\n"
            "Your `!done` message must include attestation: the concrete\n"
            "artifacts that satisfy Accountability (commit SHAs, test\n"
            "counts, files touched, paths, IDs — whatever the spec calls\n"
            "for). `!done` without attestation will be rejected.\n\n"
            "---\n\n"
        )

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

{contract}You are autonomous. Work through your task systematically, communicate
status via `messages send @.. !info`, ask for help if blocked, and
complete your objective.

## Keep the lifecycle system alive

At every checkpoint, call `tasks update <id> "progress note"` to bump
`updated_at` — this tells the lifecycle consolidator you are still
alive on this task. Even "still working on X" counts. If you go silent
for more than 15 minutes, the consolidation cycle will ping you with a
`!query` status check, and after a few ignored pings it will escalate
to `@orchestrator` or `@user`. One line every 15 minutes is enough to
stay out of that loop.

When done:

    echo "complete: summary" > ~/.metasphere/agents/{agent_id}/status
    messages send @.. !done "Completed: <summary>\\n\\nAttestation: <concrete evidence>"
"""


# ---------------------------------------------------------------------------
# Spawn (ephemeral)
# ---------------------------------------------------------------------------

def _resolve_scope(scope_path: str, project_root: Path) -> Path:
    # An absolute filesystem path already inside project_root (e.g. read
    # from an agent's scope sidecar) must have the project_root prefix
    # stripped first — otherwise lstrip("/") below would treat it as a
    # project-relative path and produce
    # <project_root>/<project_root_without_leading_slash>.
    root_str = str(project_root)
    if scope_path == root_str:
        return project_root
    if scope_path.startswith(root_str + "/"):
        scope_path = scope_path[len(root_str):]
    if scope_path.startswith("/"):
        # Project-relative absolute path.
        s = project_root / scope_path.lstrip("/")
    else:
        s = project_root / scope_path
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
    *,
    authority: str = "",
    responsibility: str = "",
    accountability: str = "",
    model: str = "",
) -> AgentRecord:
    """Create an ephemeral one-shot agent and (unless opted out) launch
    it headless via ``claude -p``.

    The three optional contract fields — ``authority``, ``responsibility``,
    ``accountability`` — implement a minimum-viable version of the
    contract-first decomposition described in DeepMind's Intelligent
    Delegation paper (arxiv 2602.11865). When supplied they're rendered
    into the agent's harness as an explicit Delegation Contract block
    and persisted to the agent dir so the parent can reload them on
    ``!done`` verification. Legacy calls that omit them keep working.

    Honors ``METASPHERE_SPAWN_NO_EXEC=1`` to skip execution.
    """
    paths = paths or resolve()
    agent_id = _normalize_name(agent_name)
    timestamp = _utcnow()

    scope_abs = _resolve_scope(scope_path, paths.project_root)

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
    # Persist the contract so the parent can reload it on !done and run
    # verification against accountability. Empty strings are fine — they
    # just mean this spawn was legacy/no-contract.
    if authority:
        _atomic_meta_write(agent_dir, "authority", authority)
    if responsibility:
        _atomic_meta_write(agent_dir, "responsibility", responsibility)
    if accountability:
        _atomic_meta_write(agent_dir, "accountability", accountability)

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
            project_root=paths.project_root,
            created_by=parent,
            assigned_to=agent_id,
        )
        _atomic_meta_write(agent_dir, "task_id", backing.id)
    except Exception:  # noqa: BLE001 — task linkage is best-effort
        pass

    # Named-agent inbox (for direct addressing).
    (paths.root / "messages" / agent_id / "inbox").mkdir(parents=True, exist_ok=True)

    harness_path = agent_dir / "harness.md"
    harness = _render_harness(
        agent_id, scope_path, parent, task, timestamp,
        authority=authority,
        responsibility=responsibility,
        accountability=accountability,
    )
    atomic_write_text(harness_path, harness)

    # No initial !task message in scope — the harness already embeds
    # the task in its `Your Task` section, so this would be redundant
    # signaling. The previous version sent one anyway, which polluted
    # the parent's scope inbox with an orphan pinned !task per spawn
    # (since parent and child share scope=/, the parent saw every
    # spawn's task message and there was nobody to act on it: the
    # child already had the task via the harness, the parent was the
    # one who issued it). See `messages_done_self_loop.md` and
    # `ephemeral_gc_no_reader_followup.md` in memory for the broader
    # "no reader" thread that this is the third instance of.

    record = AgentRecord(
        name=agent_id,
        scope=str(scope_abs),
        parent=parent,
        status=f"spawned: {task}",
        spawned_at=timestamp,
        mission_path=None,
        agent_dir=agent_dir,
    )

    # Meta is shared across every agent.spawn event variant. Contract
    # fields are always included so the events log is a usable
    # provenance ledger even for legacy spawns (empty strings are fine).
    spawn_meta = {
        "child": agent_id,
        "scope": scope_path,
        "has_contract": bool(authority or responsibility or accountability),
        "authority": authority,
        "responsibility": responsibility,
        "accountability": accountability,
    }

    no_exec = os.environ.get("METASPHERE_SPAWN_NO_EXEC", "0") == "1"
    if no_exec:
        log_event(
            "agent.spawn",
            f"{agent_id} spawned at {scope_path} (no-exec)",
            agent=parent,
            meta={**spawn_meta, "no_exec": True},
            paths=paths,
        )
        return record

    if shutil.which("claude") is None:
        log_event(
            "agent.spawn",
            f"{agent_id} harness ready at {scope_path} (claude not in PATH)",
            agent=parent,
            meta={**spawn_meta, "claude_missing": True},
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
            "METASPHERE_PROJECT_ROOT": str(paths.project_root),
            "METASPHERE_DIR": str(paths.root),
        }
    )

    cwd = scope_abs if scope_abs.is_dir() else paths.project_root
    log_fh = None
    try:
        log_fh = open(log_file, "ab")
        cmd = [
            "claude",
            "-p",
            harness,
            "--dangerously-skip-permissions",
        ]
        if model:
            cmd.extend(["--model", model])
        proc = subprocess.Popen(
            cmd,
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
        meta={**spawn_meta, "pid": proc.pid},
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


def _find_agent_dir(agent_id: str, paths: Paths) -> Optional[Path]:
    """Search for an agent directory across global + all project dirs.

    Returns the first match, preferring project-scoped agents.
    """
    # Check project agent dirs first
    if paths.projects.is_dir():
        for proj_dir in sorted(paths.projects.iterdir()):
            if not proj_dir.is_dir():
                continue
            candidate = proj_dir / "agents" / agent_id
            if candidate.is_dir():
                return candidate
    # Fall back to global
    candidate = paths.agents / agent_id
    if candidate.is_dir():
        return candidate
    return None


def _wait_for_ready(session: str, timeout_s: int = _READY_TIMEOUT_S) -> bool:
    for _ in range(timeout_s):
        if _READY_MARKER in _capture_pane(session):
            return True
        time.sleep(1)
    return False


def _submit_via_tmux(session: str, body: str) -> None:
    """Submit text to a tmux session via :mod:`metasphere.tmux`.

    Wakes pass ``escape_prefix=False``: an agent wake is a task-inject,
    not an interrupt. The pre-Escape was intended to interrupt a
    running turn so the new task becomes a NEW user-turn rather than
    queueing — but on an IDLE pane, Escape is not a no-op. It can
    trigger Claude Code's session-rating dialog ("How is Claude doing
    this session? 1: Bad 2: Fine 3: Good 0: Dismiss"), the Rewind/Undo
    menu, or other modal states. When that happens the subsequent
    typing + C-m races against the modal handler and the submit is
    eaten. 2026-04-20 test matrix: WITHOUT Escape → submits cleanly
    every time; WITH Escape on idle → triggered rating dialog on
    multiple distinct panes, typed content interleaved with modal,
    C-m unreliable.

    Claude Code queues keystrokes during a running turn and processes
    them when the turn completes — so queueing is safe. The old
    "interrupt running turn to jump the queue" rationale was wrong
    for wakes: we want our task to be processed, not to displace
    whatever the agent is doing mid-flight.
    """
    _tmux_submit(session, body, escape_prefix=False)


def wake_persistent(
    agent_name: str,
    first_task: Optional[str] = None,
    paths: Paths | None = None,
    *,
    model: str = "",
) -> AgentRecord:
    """Wake (or attach to) a persistent agent's tmux+REPL session.

    If the session is already alive, only the optional task is injected —
    no new session is created.
    """
    paths = paths or resolve()
    agent_id = _normalize_name(agent_name)

    # Resolve agent directory: check project dirs first, then global
    agent_dir = _find_agent_dir(agent_id, paths)
    if agent_dir is None:
        agent_dir = paths.agent_dir(agent_id)
    mission = agent_dir / "MISSION.md"
    if not mission.is_file():
        raise ValueError(
            f"{agent_id} is not a persistent agent (no MISSION.md at {mission})"
        )

    project = _read_text(agent_dir / "project")

    # Resolve cwd (scope) with sensible precedence:
    # 1. explicit scope file on the agent dir (rare, power-user override)
    # 2. the agent's project filesystem path (so project-scoped agents
    #    start in their own project's checkout, not the harness checkout)
    # 3. the harness project_root (global agents)
    #
    # Without #2, project-scoped agents cold-start with cwd set to the
    # metasphere-agents checkout, which means claude-code picks up
    # metasphere-agents/.claude/settings.local.json — including hooks
    # with hardcoded paths that don't belong to the target project.
    scope_str = _read_text(agent_dir / "scope")
    if not scope_str and project:
        try:
            from . import project as _project
            proj = _project.get_project(project, paths=paths)
            if proj is not None and proj.path:
                scope_str = str(proj.path)
        except Exception:
            pass
    if not scope_str:
        scope_str = str(paths.project_root)

    rec = _agent_record_from_dir(agent_dir, project=project)
    session = rec.session_name  # uses project-aware naming

    if session_alive(session):
        if first_task:
            _submit_via_tmux(session, f"[task] {first_task}")
        return rec

    # Cold start.
    _tmux_run("new-session", "-d", "-s", session, "-c", scope_str, check=False)
    _tmux_run("set-option", "-t", session, "mouse", "on")
    _tmux_run("set-option", "-t", session, "history-limit", "100000")

    # shlex.quote each value so apostrophes in scope/path don't break the shell.
    env_export = (
        f"export METASPHERE_AGENT_ID={shlex.quote(agent_id)} "
        f"METASPHERE_SCOPE={shlex.quote(scope_str)} "
        f"METASPHERE_PROJECT_ROOT={shlex.quote(str(paths.project_root))} "
        f"METASPHERE_DIR={shlex.quote(str(paths.root))}"
    )
    _tmux_run("send-keys", "-t", session, env_export, "Enter")

    from .gateway.session import _respawn_cmd

    respawn = _respawn_cmd(agent_id, model=model)
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
    # NB: send-keys C-u (no Enter) — readline kill-line. Do not "fix" by
    # adding Enter.
    _tmux_run("send-keys", "-t", session, "C-u")
    time.sleep(0.2)

    if first_task:
        _submit_via_tmux(session, f"[task] {first_task}")

    return _agent_record_from_dir(agent_dir, project=project)


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


def reap_dormant(
    paths: Paths | None = None,
    max_idle_seconds: int = 86400,
) -> list[str]:
    """Transition persistent agents to dormant when their tmux session
    has been idle longer than ``max_idle_seconds``.

    For each qualifying agent:
    - Write ``status = "dormant: idle Ns (auto-ttl at <utc>)"`` to the
      agent dir so ``metasphere status`` and human observers can see
      why the session went away.
    - ``tmux kill-session -t <session>`` (silent no-op if already gone).
    - Persona files (``MISSION.md``, ``SOUL.md``, ``LEARNINGS.md``,
      ``HEARTBEAT.md``, contract sidecars) are preserved — a future
      ``metasphere agent wake <name>`` restarts cleanly from them.

    Returns the list of agent names transitioned. Failures on a single
    agent do not abort the others: this runs on a daemon tick and must
    never exit the gateway loop.
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
        if idle is None or idle <= max_idle_seconds:
            continue
        if agent.agent_dir is not None:
            try:
                _atomic_meta_write(
                    agent.agent_dir,
                    "status",
                    f"dormant: idle {idle}s (auto-ttl at {_utcnow()})",
                )
            except OSError:
                pass
        _tmux_run("kill-session", "-t", session)
        try:
            log_event(
                "agent.dormant",
                f"{agent.name} dormant after {idle}s idle — tmux session killed",
                agent=agent.name,
                meta={"idle_seconds": idle, "session": session},
                paths=paths,
            )
        except Exception:
            pass
        out.append(agent.name)
    return out


# ---------------------------------------------------------------------------
# Crash reaper (silent-death detection)
#
# Counterpart to ``reap_dormant``: that handles the well-behaved case
# where a persistent agent's tmux session is alive but idle. This handles
# the misbehaved case where the agent process died without writing a
# terminal status — leaving a stale ``status: spawned: ...`` (or
# ``working:``) on disk while the pid is gone and no tmux session
# exists. Without this hook, silent-death agents are invisible until a
# human notices the orphan status file.
#
# 2026-04-21 spawn-stall incidents (PR #35 followup): @service-restart-impl
# died at spawn-time before writing any output; the only signal was a
# stale ``status=spawned`` with no tmux session. ``reap_crashed`` closes
# that gap by promoting the implicit "no pid + no session" state to an
# explicit ``crashed:`` status and pushing a ``!alert`` to the parent.
# ---------------------------------------------------------------------------

#: Status prefixes treated as terminal — ``reap_crashed`` will not
#: re-transition an agent already in one of these states. Membership
#: only; order is irrelevant.
_TERMINAL_STATUS_PREFIXES = (
    "complete:",
    "dormant:",
    "crashed:",
    "failed:",
)


def _pid_alive(pid: int) -> bool:
    """True iff process ``pid`` exists.

    Uses signal-0 — the canonical no-op liveness probe (see kill(2)).
    A ``PermissionError`` means the process exists but is owned by
    another uid; that still counts as alive (we cannot disprove it).
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def reap_crashed(paths: Paths | None = None) -> list[str]:
    """Detect agents that died silently and promote them to ``crashed:``.

    For every non-terminal agent with a recorded pid file:
    - If both the pid is dead (``os.kill(pid, 0)`` → ``ProcessLookupError``)
      AND the agent's tmux session is gone, atomically rewrite ``status``
      to ``crashed: pid <N> dead, session gone`` and send a ``!alert``
      message to the agent's parent (read from the ``parent`` sidecar)
      via :func:`metasphere.messages.send_message`.
    - Skips agents already in a terminal status
      (``complete:`` / ``dormant:`` / ``crashed:`` / ``failed:``).
    - Skips agents with no pid file (no liveness signal recorded — this
      is the legacy ``METASPHERE_SPAWN_NO_EXEC`` case and the
      pre-pid-write window during spawn).
    - Skips the parent ``!alert`` if the ``parent`` sidecar is missing
      (no addressee), but still writes the ``crashed:`` status.

    Per-agent failures are swallowed — this runs on a daemon tick and
    must never abort the gateway loop. Returns the list of agent names
    that were transitioned this sweep.
    """
    paths = paths or resolve()
    out: list[str] = []
    for agent in list_agents(paths):
        status = agent.status or ""
        if any(status.startswith(p) for p in _TERMINAL_STATUS_PREFIXES):
            continue
        if agent.pid_file is None:
            continue
        try:
            pid = int(agent.pid_file.read_text(encoding="utf-8").strip())
        except (OSError, ValueError):
            continue
        if _pid_alive(pid):
            continue
        session = agent.session_name
        if session_alive(session):
            continue

        # Both signals say the agent is gone — silent death.
        reason = f"crashed: pid {pid} dead, session gone"
        if agent.agent_dir is None:
            continue
        try:
            _atomic_meta_write(agent.agent_dir, "status", reason)
        except OSError:
            # If we can't even write the status, the alert would be
            # misleading — bail on this agent and let the next sweep
            # try again.
            continue

        if agent.parent:
            try:
                from . import messages as _messages
                _messages.send_message(
                    target=agent.parent,
                    label="!alert",
                    body=(
                        f"{agent.name} silent death detected by "
                        f"reap_crashed: pid {pid} no longer running and "
                        f"tmux session {session} not present. Status "
                        "transitioned to crashed."
                    ),
                    from_agent=agent.name,
                    paths=paths,
                    wake=False,
                )
            except Exception:
                pass

        try:
            log_event(
                "agent.crashed",
                f"{agent.name} silent death — pid {pid} dead, "
                f"session {session} gone",
                agent=agent.name,
                meta={"pid": pid, "session": session, "parent": agent.parent},
                paths=paths,
            )
        except Exception:
            pass
        out.append(agent.name)
    return out


# ---------------------------------------------------------------------------
# !done delivery hook (session hygiene)
#
# When an ephemeral agent sends !done it has no reason to keep its
# tmux pane (if any) or process linkage around — the parent's
# Accountability check runs against artifacts on disk, not against the
# child process. Persistent agents must NOT be killed on !done:
# they're long-lived collaborators and may well send multiple !dones
# over their lifetime. Their session lifecycle is governed by
# ``reap_dormant`` (idle-TTL) instead.
# ---------------------------------------------------------------------------

def on_done_delivered(sender: str, paths: Paths | None = None) -> Optional[str]:
    """Fired from :func:`metasphere.messages.send_message` right after a
    ``!done`` message is delivered to its target. Kills the sender's
    tmux session and clears runtime state pointers iff the sender is
    an ephemeral agent (no ``MISSION.md``). Persistent senders are a
    no-op — their lifecycle is governed by idle-TTL dormancy.

    Returns the killed session name if an ephemeral cleanup ran, else
    ``None``. Persona files (``harness.md``, ``authority``,
    ``responsibility``, ``accountability``, ``scope``, ``parent``,
    ``spawned_at``) are preserved so the GC log and later audits can
    still reconstruct what the agent was contracted to do.
    """
    paths = paths or resolve()
    if not sender or not isinstance(sender, str):
        return None
    # Skip non-agent senders: user, scope targets, parent aliases.
    # A real agent id looks like "@name" (no slash, no dots).
    if not sender.startswith("@"):
        return None
    if sender in ("@user", "@.", "@.."):
        return None
    if "/" in sender:  # scope-path like "@/abs/path/"
        return None
    agent_id = _normalize_name(sender)
    agent_dir = _find_agent_dir(agent_id, paths)
    if agent_dir is None:
        return None
    project = _read_text(agent_dir / "project")
    rec = _agent_record_from_dir(agent_dir, project=project)
    if rec.is_persistent:
        return None  # persistent agents are not killed on !done

    session = rec.session_name
    # Kill-session is idempotent: rc=1 when session absent, which is
    # exactly what we want for headless-Popen ephemerals that never
    # had a tmux pane in the first place.
    _tmux_run("kill-session", "-t", session)

    # Clear runtime state pointers so a future spawn with the same name
    # bootstraps from scratch. Persona/contract/harness files survive.
    for name in ("pid", "task_id"):
        try:
            f = agent_dir / name
            if f.exists():
                f.unlink()
        except OSError:
            pass
    try:
        _atomic_meta_write(agent_dir, "status", "complete: !done delivered")
    except OSError:
        pass

    try:
        log_event(
            "agent.ephemeral_done",
            f"{agent_id} !done delivered — tmux killed + runtime state cleared",
            agent=agent_id,
            meta={"session": session},
            paths=paths,
        )
    except Exception:
        pass
    return session
