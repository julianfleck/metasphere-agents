"""Slash command dispatch for the telegram bot.

Each command returns a string (the body to send back). The dispatcher
calls into other ``metasphere/*`` modules where they exist; for modules
that haven't landed yet in the parallel rewrite, we shell out to the
existing bash scripts so the bot is fully functional during the
porting period.

Adding a new command:
1. Define a ``cmd_<name>(args, ctx)`` function returning a string.
2. Register it in the COMMANDS dict at the bottom.

Commands receive a ``Context`` carrying chat_id, thread_id, from_user
so they can compose replies that respect forum topics.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass
from typing import Callable, Dict, Optional

# Telegram-controlled identifiers that get interpolated into filesystem
# paths or argv must match this; rejects "..", "/", whitespace, etc.
_AGENT_RE = re.compile(r"^@[A-Za-z0-9_-]+$")
_LABEL_RE = re.compile(r"^![A-Za-z0-9_-]+$")

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPTS_DIR = os.path.join(REPO_ROOT, "scripts")
METASPHERE_DIR = os.path.expanduser("~/.metasphere")


@dataclass
class Context:
    chat_id: int
    from_user: str
    thread_id: Optional[int] = None


def _run(cmd: list[str], env: Optional[dict] = None, timeout: int = 15) -> str:
    """Run a subprocess and return combined output, truncated."""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, **(env or {})},
        )
        out = (proc.stdout or "") + (proc.stderr or "")
        return out.strip() or "(no output)"
    except subprocess.TimeoutExpired:
        return f"(timed out after {timeout}s)"
    except FileNotFoundError as e:
        return f"(missing: {e.filename})"


# --- Commands -------------------------------------------------------------

def cmd_start(args: str, ctx: Context) -> str:
    return (
        "Metasphere (rewrite)\n\n"
        "Commands:\n"
        "/status /tasks /agents /messages /inbox\n"
        "/send @agent !label msg\n"
        "/project [sub args...] | /cam query | /groups | /link\n"
        "/events | /tree | /spot | /ping | /help\n\n"
        "Or just message me directly."
    )


def cmd_help(args: str, ctx: Context) -> str:
    return cmd_start(args, ctx)


def cmd_ping(args: str, ctx: Context) -> str:
    return "pong"


def cmd_status(args: str, ctx: Context) -> str:
    # Prefer Python entry if it lands; fall back to bash.
    try:
        from metasphere import status as ms_status  # type: ignore

        return ms_status.summary()  # pragma: no cover
    except Exception:
        pass
    return _run([os.path.join(SCRIPTS_DIR, "metasphere"), "status"])


def cmd_tasks(args: str, ctx: Context) -> str:
    try:
        from metasphere import tasks as ms_tasks  # type: ignore

        return ms_tasks.list_tasks_text()  # pragma: no cover
    except Exception:
        pass
    return _run([os.path.join(SCRIPTS_DIR, "tasks")])


def cmd_messages(args: str, ctx: Context) -> str:
    try:
        from metasphere import messages as ms_messages  # type: ignore

        return ms_messages.list_messages_text()  # pragma: no cover
    except Exception:
        pass
    return _run([os.path.join(SCRIPTS_DIR, "messages")])


def cmd_agents(args: str, ctx: Context) -> str:
    agents_dir = os.path.join(METASPHERE_DIR, "agents")
    if not os.path.isdir(agents_dir):
        return "No agents registered."
    lines = ["Agents:"]
    for entry in sorted(os.listdir(agents_dir)):
        if not entry.startswith("@"):
            continue
        sf = os.path.join(agents_dir, entry, "status")
        status = ""
        if os.path.exists(sf):
            with open(sf) as f:
                status = f.read().strip()
        lines.append(f"• {entry}: {status}")
    return "\n".join(lines)


def cmd_inbox(args: str, ctx: Context) -> str:
    target = (args.strip() or "@orchestrator")
    if not _AGENT_RE.match(target):
        return "Invalid agent name"
    scope_file = os.path.join(METASPHERE_DIR, "agents", target, "scope")
    scope = METASPHERE_DIR
    if os.path.exists(scope_file):
        with open(scope_file) as f:
            scope = f.read().strip() or scope
    return _run(
        [os.path.join(SCRIPTS_DIR, "messages")],
        env={"METASPHERE_SCOPE": scope, "METASPHERE_AGENT_ID": target},
    )


def cmd_send(args: str, ctx: Context) -> str:
    parts = args.split(None, 2)
    if len(parts) < 3:
        return "Usage: /send @target !label message"
    target, label, message = parts
    if not _AGENT_RE.match(target):
        return "Invalid target (expected @name)"
    if not _LABEL_RE.match(label):
        return "Invalid label (expected !name)"
    return _run(
        [os.path.join(SCRIPTS_DIR, "messages"), "send", target, label, message],
        env={"METASPHERE_AGENT_ID": "@user"},
    )


def cmd_cam(args: str, ctx: Context) -> str:
    if not args.strip():
        return "Usage: /cam <query>"
    return _run(["cam", "search", args.strip(), "--limit", "5"])


def cmd_groups(args: str, ctx: Context) -> str:
    script = os.path.join(SCRIPTS_DIR, "metasphere-telegram-groups")
    if not args.strip():
        return _run([script, "list"])
    sub = args.strip().split()
    return _run([script, *sub])


def cmd_link(args: str, ctx: Context) -> str:
    name = args.strip().strip('"')
    if ctx.thread_id and not name:
        chat_clean = str(ctx.chat_id).removeprefix("-100")
        return f"Topic link: https://t.me/c/{chat_clean}/{ctx.thread_id}"
    if not name:
        return 'Usage: /link "Project Name"'
    script = os.path.join(SCRIPTS_DIR, "metasphere-telegram-groups")
    return _run([script, "workspace", "project", name])


def cmd_events(args: str, ctx: Context) -> str:
    return _run([os.path.join(SCRIPTS_DIR, "metasphere-events"), "tail", "10"])


def cmd_tree(args: str, ctx: Context) -> str:
    return _run([os.path.join(SCRIPTS_DIR, "metasphere-agent"), "tree"])


def cmd_project(args: str, ctx: Context) -> str:
    """Dispatch to ``metasphere project`` subcommands.

    Bare ``/project`` -> list. ``/project <sub> [args...]`` -> shells out.
    Quoted args are honored via shlex so users can pass messages.
    """
    import shlex
    sub_argv = shlex.split(args) if args.strip() else ["list"]
    return _run(["metasphere", "project", *sub_argv])


def cmd_spot(args: str, ctx: Context) -> str:
    return _run(
        [
            "ssh", "-p", "2323", "-o", "ConnectTimeout=5", "-o", "BatchMode=yes",
            "data.basicbold.de",
            "sudo machinectl status openclaw 2>/dev/null | head -20",
        ],
        timeout=10,
    )


COMMANDS: Dict[str, Callable[[str, Context], str]] = {
    "start": cmd_start,
    "help": cmd_help,
    "h": cmd_help,
    "ping": cmd_ping,
    "status": cmd_status,
    "s": cmd_status,
    "tasks": cmd_tasks,
    "t": cmd_tasks,
    "messages": cmd_messages,
    "m": cmd_messages,
    "agents": cmd_agents,
    "a": cmd_agents,
    "inbox": cmd_inbox,
    "send": cmd_send,
    "cam": cmd_cam,
    "groups": cmd_groups,
    "link": cmd_link,
    "events": cmd_events,
    "tree": cmd_tree,
    "spot": cmd_spot,
    "project": cmd_project,
    "p": cmd_project,
}


def dispatch(text: str, ctx: Context) -> Optional[str]:
    """Dispatch a slash command. Returns reply text, or None if not a command."""
    if not text.startswith("/"):
        return None
    body = text[1:]
    name, _, args = body.partition(" ")
    # Strip @botname suffix Telegram appends in groups
    name = name.split("@", 1)[0].lower()
    fn = COMMANDS.get(name)
    if fn is None:
        return "Unknown command. Try /help"
    try:
        return fn(args, ctx)
    except Exception as e:  # pragma: no cover - defensive
        return f"Command /{name} failed: {e}"
