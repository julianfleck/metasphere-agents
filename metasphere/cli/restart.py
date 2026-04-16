"""CLI: ``metasphere restart``.

Wholesale restart of systemd daemons and/or agent tmux sessions.

Usage::

    metasphere restart                 # daemons + all alive agent sessions
    metasphere restart <agent-name>    # restart one agent's tmux session
    metasphere restart -h | --help

Behavior:
    Without args: restart ``metasphere-heartbeat``, ``metasphere-gateway``
    and ``metasphere-schedule`` (systemd ``--user``), then kill+respawn
    every alive persistent agent's tmux session, including
    ``@orchestrator``. If invoked from inside the orchestrator pane,
    that restart will kill the caller — a warning is printed first
    and the orchestrator is restarted last.

    With ``<agent-name>``: kill just that agent's tmux session and
    re-spawn it via :func:`metasphere.agents.wake_persistent`. Daemons
    untouched. Unknown agents get a suggestion of near matches via
    :mod:`difflib`.

Wraps the long-form recipe Julian was hand-typing (``systemctl --user
restart metasphere-{heartbeat,gateway,schedule}`` + per-agent
``tmux kill-session`` + manual ``metasphere agent wake``).
"""

from __future__ import annotations

import difflib
import os
import shutil
import subprocess
import sys
from typing import Iterable

from metasphere import agents as _agents
from metasphere.paths import Paths, resolve

_DAEMONS = (
    "metasphere-heartbeat",
    "metasphere-gateway",
    "metasphere-schedule",
)

_HELP = __doc__ or ""


def _systemctl(*args: str) -> int:
    """Invoke ``systemctl --user`` and return the rc. 1 if systemctl is
    missing (no service manager → nothing to restart)."""
    if not shutil.which("systemctl"):
        return 1
    proc = subprocess.run(
        ["systemctl", "--user", *args],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc.returncode


def _restart_daemon(name: str) -> bool:
    """Restart one systemd unit. Returns True on success."""
    return _systemctl("restart", name) == 0


def _restart_systemd_daemons() -> dict[str, bool]:
    """Restart all three daemons. Return ``{name: ok}``."""
    return {d: _restart_daemon(d) for d in _DAEMONS}


def _tmux_kill(session: str) -> bool:
    """``tmux kill-session -t <session>``. Returns True on rc=0."""
    tmux = shutil.which("tmux")
    if not tmux:
        return False
    proc = subprocess.run(
        [tmux, "kill-session", "-t", session],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return proc.returncode == 0


def _restart_agent_session(
    agent_name: str, paths: Paths
) -> tuple[bool, str]:
    """Kill the agent's tmux session (if alive) and re-spawn via
    ``wake_persistent``. Returns ``(ok, message)``.

    For ``@orchestrator`` (which uses the gateway-managed session name
    ``metasphere-orchestrator`` and the gateway respawn flow), routes
    through :mod:`metasphere.gateway.session.start_session` instead of
    ``wake_persistent``.
    """
    agent_id = _agents._normalize_name(agent_name)
    if agent_id == "@orchestrator":
        from metasphere.gateway.session import SESSION_NAME, start_session

        if _agents.session_alive(SESSION_NAME):
            _tmux_kill(SESSION_NAME)
        ok = start_session(paths)
        return ok, (
            f"@orchestrator: tmux killed + gateway start_session "
            f"(ok={ok})"
        )

    session = _agents.session_name_for(agent_id)
    was_alive = _agents.session_alive(session)
    if was_alive:
        _tmux_kill(session)
    try:
        _agents.wake_persistent(agent_id, paths=paths)
    except ValueError as e:
        return False, f"{agent_id}: {e}"
    return True, (
        f"{agent_id}: tmux killed (was_alive={was_alive}) + wake_persistent ok"
    )


def _alive_persistent_agent_ids(paths: Paths) -> list[str]:
    """Return the list of persistent agents whose tmux session is alive,
    @orchestrator first if present so it gets restarted last."""
    out: list[str] = []
    for agent in _agents.list_agents(paths):
        if not getattr(agent, "is_persistent", False):
            continue
        if _agents.session_alive(agent.session_name):
            out.append(agent.name)
    # Also check @orchestrator (uses a different session name).
    from metasphere.gateway.session import SESSION_NAME, session_alive as _o_alive
    if _o_alive(SESSION_NAME) and "@orchestrator" not in out:
        out.append("@orchestrator")
    # Restart @orchestrator LAST so the caller (likely inside it) doesn't
    # die before everything else is restarted.
    out.sort(key=lambda a: (a == "@orchestrator", a))
    return out


def _suggest_agent_name(name: str, candidates: Iterable[str]) -> list[str]:
    pool = sorted(set(candidates))
    norm = name.lstrip("@")
    return difflib.get_close_matches(norm, [c.lstrip("@") for c in pool],
                                     n=3, cutoff=0.4)


def _inside_orchestrator() -> bool:
    """Best-effort: True if we're running inside the @orchestrator pane."""
    if os.environ.get("METASPHERE_AGENT_ID") == "@orchestrator":
        return True
    # Also check tmux pane env: TMUX is set + session name matches.
    tmux_env = os.environ.get("TMUX")
    if tmux_env and "metasphere-orchestrator" in tmux_env:
        return True
    return False


def _print_daemon_results(results: dict[str, bool]) -> int:
    """Print one line per daemon, return rc (0 if all ok, 1 otherwise)."""
    rc = 0
    for name, ok in results.items():
        marker = "ok" if ok else "FAILED"
        print(f"  {name}: {marker}")
        if not ok:
            rc = 1
    return rc


def _restart_all(paths: Paths) -> int:
    """Restart all daemons + all alive agent sessions."""
    if _inside_orchestrator():
        print(
            "WARNING: invoked from inside @orchestrator — the "
            "orchestrator restart at the end will kill this process. "
            "All earlier work will complete first.",
            file=sys.stderr,
        )

    print("restarting systemd daemons:")
    rc = _print_daemon_results(_restart_systemd_daemons())

    agents_to_restart = _alive_persistent_agent_ids(paths)
    if not agents_to_restart:
        print("\nno alive agent sessions to restart.")
        return rc

    print(f"\nrestarting {len(agents_to_restart)} agent session(s):")
    for agent_id in agents_to_restart:
        ok, msg = _restart_agent_session(agent_id, paths)
        marker = "ok" if ok else "FAILED"
        print(f"  {marker}: {msg}")
        if not ok:
            rc = 1
    return rc


def _restart_one(agent_name: str, paths: Paths) -> int:
    """Restart one named agent. Suggest near matches on miss."""
    agent_id = _agents._normalize_name(agent_name)
    known = [a.name for a in _agents.list_agents(paths)]
    known.append("@orchestrator")
    if agent_id not in known:
        suggestions = _suggest_agent_name(agent_id, known)
        msg = f"unknown agent: {agent_id}"
        if suggestions:
            sugg = ", ".join("@" + s for s in suggestions)
            msg += f"\ndid you mean: {sugg}?"
        print(msg, file=sys.stderr)
        return 2

    ok, line = _restart_agent_session(agent_id, paths)
    print(line)
    return 0 if ok else 1


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if args and args[0] in ("-h", "--help"):
        sys.stdout.write(_HELP)
        return 0

    paths = resolve()
    if not args:
        return _restart_all(paths)
    if len(args) == 1:
        return _restart_one(args[0], paths)
    print("usage: metasphere restart [<agent-name>]", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
