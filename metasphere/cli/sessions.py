"""``metasphere sessions`` — multi-agent tmux viewer.

Builds and tears down the "viewer" tmux session: a single tmux window
that mirrors every persistent agent's pane side-by-side for at-a-glance
observability. The viewer is read-only — input goes to its source
panes, this just composes a layout. Use the singular ``metasphere
session`` (this module's sibling) for write operations on one agent.
"""

from __future__ import annotations

DESCRIPTION = "Multi-agent tmux viewer: build, list, or tear down the viewer."

USAGE = """\
Usage: metasphere sessions <command>

Commands:
  all            Build and attach a viewer tmux session showing every
                 alive persistent agent as a linked window. Idempotent:
                 re-running drops the old viewer and rebuilds.
  list           Print alive persistent agents and their tmux session
                 names.
  ls             Alias for `list`.
  kill-viewer    Tear down the viewer session without touching the
                 source sessions.

The viewer is named `metasphere-all`. Source sessions are untouched
(tmux link-window is non-destructive); killing the viewer just drops
the linked references. Detach with Ctrl+b d as usual.
"""


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
        sys.stdout.write(USAGE)
        return 0 if args else 2
    cmd, rest = args[0], args[1:]
    handler = _SUBCOMMANDS.get(cmd)
    if handler is None:
        print(f"unknown subcommand: {cmd}", file=sys.stderr)
        sys.stderr.write(USAGE)
        return 2
    return handler(rest)


if __name__ == "__main__":
    raise SystemExit(main())
