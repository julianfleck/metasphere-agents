"""CLI: ``metasphere sessions`` — multi-agent observability.

    sessions all           Build and attach a viewer tmux session that
                           shows every alive persistent agent as a
                           linked window. Idempotent: re-running drops
                           the old viewer and rebuilds from current state.
    sessions list          Print alive persistent agents and their tmux
                           session names.
    sessions kill-viewer   Tear down the viewer session without
                           touching source sessions.

The viewer is named ``metasphere-all``. Source sessions are untouched;
``tmux link-window`` is non-destructive, and killing the viewer just
drops the linked references. Detach with ``Ctrl+b d`` as usual.
"""

from __future__ import annotations

import sys

from metasphere.session import (
    VIEWER_SESSION_NAME,
    attach_viewer,
    build_viewer_session,
    kill_viewer_session,
    list_alive_persistent_agents,
)


def _cmd_all(_rest: list[str]) -> int:
    viewer, linked = build_viewer_session()
    if not linked:
        print("no alive persistent agents to attach", file=sys.stderr)
        return 1
    names = ", ".join(a.name for a in linked)
    print(
        f"Attaching {len(linked)} agents: {names} "
        f"(Ctrl+b d to detach)"
    )
    sys.stdout.flush()
    return attach_viewer(viewer)


def _cmd_list(_rest: list[str]) -> int:
    alive = list_alive_persistent_agents()
    if not alive:
        print("(no alive persistent agents)")
        return 0
    for agent, sname in alive:
        label = agent.name
        if agent.project:
            label = f"{agent.name} [{agent.project}]"
        print(f"{label:32} {sname}")
    return 0


def _cmd_kill_viewer(_rest: list[str]) -> int:
    if kill_viewer_session():
        print(f"killed viewer session {VIEWER_SESSION_NAME}")
        return 0
    print(f"no viewer session {VIEWER_SESSION_NAME}", file=sys.stderr)
    return 1


_SUBCOMMANDS = {
    "all": _cmd_all,
    "list": _cmd_list,
    "ls": _cmd_list,
    "kill-viewer": _cmd_kill_viewer,
}


def main(argv: list[str] | None = None) -> int:
    args = list(argv if argv is not None else sys.argv[1:])
    if not args or args[0] in ("--help", "-h"):
        print(__doc__ or "")
        return 0 if args else 2
    cmd, rest = args[0], args[1:]
    handler = _SUBCOMMANDS.get(cmd)
    if handler is None:
        print(f"unknown subcommand: {cmd}", file=sys.stderr)
        print(__doc__ or "", file=sys.stderr)
        return 2
    return handler(rest)


if __name__ == "__main__":
    raise SystemExit(main())
