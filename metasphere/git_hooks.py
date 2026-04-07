"""Install/uninstall git hook shims (port of scripts/metasphere-git-hooks).

Each installed hook is a small shell shim that execs back into
``python -m metasphere.cli.git_hooks <event> [args...]`` so the
substantive logic stays in Python and tests don't have to shell out.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path
from typing import Iterable

from .events import log_event
from .paths import Paths, resolve

HOOKS = ("pre-commit", "post-commit", "post-checkout", "pre-push")
_MARKER = "# Metasphere managed hook"


def _shim(event: str) -> str:
    # Bake the absolute interpreter path captured at install time so the
    # shim works inside venvs / pipx installs (M4, wave-4 review).
    py = sys.executable or "python3"
    return (
        "#!/bin/bash\n"
        f"{_MARKER}\n"
        f'exec {py} -m metasphere.cli.git_hooks {event} "$@"\n'
    )


def _hooks_dir(repo_path: Path) -> Path:
    return repo_path / ".git" / "hooks"


def install_hooks(repo_path: Path) -> list[str]:
    """Install metasphere shims into ``.git/hooks``.

    Existing non-metasphere hooks are backed up to ``<hook>.backup``.
    Returns the list of hooks written.
    """
    repo_path = Path(repo_path)
    if not (repo_path / ".git").is_dir():
        raise FileNotFoundError(f"not a git repository: {repo_path}")
    hooks_dir = _hooks_dir(repo_path)
    hooks_dir.mkdir(parents=True, exist_ok=True)

    written: list[str] = []
    for hook in HOOKS:
        target = hooks_dir / hook
        if target.exists() and _MARKER not in target.read_text(errors="replace").splitlines():
            target.replace(target.with_suffix(target.suffix + ".backup"))
        target.write_text(_shim(hook))
        target.chmod(target.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
        written.append(hook)
    return written


def uninstall_hooks(repo_path: Path) -> list[str]:
    repo_path = Path(repo_path)
    hooks_dir = _hooks_dir(repo_path)
    if not hooks_dir.is_dir():
        return []
    removed: list[str] = []
    for hook in HOOKS:
        target = hooks_dir / hook
        if target.exists() and _MARKER in target.read_text(errors="replace").splitlines():
            target.unlink()
            removed.append(hook)
            backup = target.with_suffix(target.suffix + ".backup")
            if backup.exists():
                backup.replace(target)
    return removed


def hooks_status(repo_path: Path) -> dict[str, str]:
    """Return ``{hook: state}`` where state is ``metasphere|other|missing``."""
    repo_path = Path(repo_path)
    hooks_dir = _hooks_dir(repo_path)
    out: dict[str, str] = {}
    for hook in HOOKS:
        target = hooks_dir / hook
        if not target.exists():
            out[hook] = "missing"
        elif _MARKER in target.read_text(errors="replace").splitlines():
            out[hook] = "metasphere"
        else:
            out[hook] = "other"
    return out


# ---- hook event handlers (called by cli.git_hooks) ----

def _git_out(*args: str, cwd: Path | None = None) -> str:
    try:
        return subprocess.run(
            ["git", *args],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            check=False,
            cwd=str(cwd) if cwd else None,
        ).stdout.strip()
    except FileNotFoundError:
        return ""


def handle_pre_commit(*, paths: Paths | None = None) -> int:
    paths = paths or resolve()
    log_event("git.pre-commit", "Pre-commit check",
              meta={"repo": Path.cwd().name}, paths=paths)
    return 0


def handle_post_commit(*, paths: Paths | None = None) -> int:
    paths = paths or resolve()
    sha = _git_out("rev-parse", "HEAD")
    msg = _git_out("log", "-1", "--pretty=%s")
    author = _git_out("log", "-1", "--pretty=%an")
    files = _git_out("diff-tree", "--no-commit-id", "--name-only", "-r", "HEAD")
    file_count = len([f for f in files.splitlines() if f]) if files else 0
    repo = Path(_git_out("rev-parse", "--show-toplevel") or ".").name
    log_event(
        "git.commit", msg or "(no message)",
        meta={"sha": sha[:8], "author": author, "files": file_count, "repo": repo},
        paths=paths,
    )
    return 0


def handle_post_checkout(prev: str = "", new: str = "", branch_flag: str = "0",
                         *, paths: Paths | None = None) -> int:
    if branch_flag != "1":
        return 0
    paths = paths or resolve()
    branch = _git_out("rev-parse", "--abbrev-ref", "HEAD")
    repo = Path(_git_out("rev-parse", "--show-toplevel") or ".").name
    log_event("git.checkout", f"Switched to {branch}",
              meta={"branch": branch, "repo": repo}, paths=paths)
    return 0


def handle_pre_push(remote: str = "", url: str = "",
                    *, paths: Paths | None = None) -> int:
    paths = paths or resolve()
    branch = _git_out("rev-parse", "--abbrev-ref", "HEAD")
    repo = Path(_git_out("rev-parse", "--show-toplevel") or ".").name
    log_event("git.push", f"Pushing {branch} to {remote}",
              meta={"branch": branch, "remote": remote, "repo": repo}, paths=paths)
    return 0
