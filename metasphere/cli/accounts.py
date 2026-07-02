"""``metasphere accounts`` — manage Anthropic OAuth credential profiles.

The harness keeps a per-profile copy of ``~/.claude/.credentials.json``
under ``~/.metasphere/accounts/<name>/credentials.json``. The live
credential file is a symlink that points at one of the profiles.

Linux only — macOS stores Anthropic OAuth tokens in the keychain, not
as a file, so symlink-swap doesn't apply. Mode 0600 is enforced on
every write to a credential file.
"""

from __future__ import annotations

DESCRIPTION = "Manage Anthropic OAuth credential profiles via symlink swap."

USAGE = """\
Usage: metasphere accounts <command> [args...]

Commands:
  list                          List profiles in ~/.metasphere/accounts/.
  current                       Print the active profile name.
  switch <name>                 Atomically retarget the credentials
                                symlink to <name>.
  add <name> [--from <path>] [--force]
                                Capture a profile from --from <path>,
                                or snapshot the live credentials file.
                                --force overwrites an existing profile.
  status                        Show symlink integrity, target file
                                presence, and per-profile mode + mtime.

Linux only. macOS short-circuits with a clear error.
"""


import argparse
import os
import re
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional


# Module-level constants. Tests monkeypatch these to redirect into
# ``tmp_path``; production code reads them from the operator's home.
ACCOUNTS_DIR = Path.home() / ".metasphere" / "accounts"
LIVE_CRED = Path.home() / ".claude" / ".credentials.json"

CRED_FILENAME = "credentials.json"
SECURE_MODE = 0o600


_NAME_INVALID_RE = re.compile(r"[/\\\x00]")


def _validate_profile_name(name: str) -> None:
    """Raise ``ValueError`` if ``name`` would be unsafe to register.

    Same shape as ``project._validate_name`` /
    ``agents._validate_agent_name``: argparse will reject a bare
    ``--bogus`` as an unknown option, but ``accounts add -- --bogus``
    happily makes ``--bogus`` a positional, and the previous code
    would create ``~/.metasphere/accounts/--bogus/credentials.json``
    on disk. Reject leading-dash, empty / whitespace-only, path
    separators, and ``.``/``..``.
    """
    if not name or not name.strip():
        raise ValueError("profile name must be non-empty")
    if name.startswith("-"):
        raise ValueError(
            f"invalid profile name: {name!r} "
            f"(must not start with '-' — looks like a CLI flag)"
        )
    if name in (".", ".."):
        raise ValueError(
            f"invalid profile name: {name!r} (must not be '.' or '..')"
        )
    if _NAME_INVALID_RE.search(name):
        raise ValueError(
            f"invalid profile name: {name!r} "
            f"(must not contain /, \\, or null)"
        )


# ---------------------------------------------------------------------------
# Platform gate
# ---------------------------------------------------------------------------


def _refuse_on_darwin() -> Optional[int]:
    """Print a polite refusal on macOS and return exit code 2.

    Returns ``None`` on Linux so callers can proceed.
    """
    if sys.platform == "darwin":
        sys.stderr.write(
            "metasphere accounts: macOS is not supported (v1).\n"
            "Anthropic stores OAuth tokens in the keychain there, not as a\n"
            "file, so the symlink-swap model doesn't apply. Use the keychain\n"
            "directly or wait for a future macOS branch.\n"
        )
        return 2
    return None


# ---------------------------------------------------------------------------
# Helpers (pure)
# ---------------------------------------------------------------------------


def _profile_path(name: str) -> Path:
    """Resolve the on-disk credentials file for ``name``."""
    return ACCOUNTS_DIR / name / CRED_FILENAME


def _list_profiles() -> List[str]:
    """Return profile names sorted alphabetically. Empty if dir missing."""
    if not ACCOUNTS_DIR.is_dir():
        return []
    out = []
    for child in sorted(ACCOUNTS_DIR.iterdir()):
        if child.is_dir() and (child / CRED_FILENAME).is_file():
            out.append(child.name)
    return out


def _resolve_current() -> Optional[str]:
    """Return the profile name the live credentials symlink points at,
    or ``None`` if the link is missing / points outside ``ACCOUNTS_DIR``
    / is a regular file (unmanaged)."""
    if not LIVE_CRED.is_symlink():
        return None
    try:
        target = LIVE_CRED.resolve(strict=False)
    except OSError:
        return None
    try:
        rel = target.relative_to(ACCOUNTS_DIR.resolve(strict=False))
    except ValueError:
        return None
    # Expect <profile>/credentials.json — exactly two components.
    parts = rel.parts
    if len(parts) != 2 or parts[1] != CRED_FILENAME:
        return None
    return parts[0]


def _atomic_symlink(target: Path, link: Path) -> None:
    """Create or replace ``link`` so it points at ``target``, atomically.

    Writes a temp symlink in the same directory, then ``os.replace``-es
    it into the final name. ``os.replace`` is atomic on POSIX; if it
    fails partway, the original link (if any) is preserved.
    """
    link.parent.mkdir(parents=True, exist_ok=True)
    # tempfile.mkstemp would create a regular file, not what we want;
    # generate a unique sibling name by hand.
    fd, tmp_name = tempfile.mkstemp(prefix=".cred-swap-", dir=str(link.parent))
    os.close(fd)
    os.unlink(tmp_name)  # we just wanted a unique name
    os.symlink(str(target), tmp_name)
    os.replace(tmp_name, str(link))


def _enforce_mode(path: Path, mode: int = SECURE_MODE) -> None:
    """``chmod`` to ``mode``. Best-effort: a remote filesystem that
    refuses ``chmod`` shouldn't break the whole flow."""
    try:
        os.chmod(path, mode)
    except OSError:
        pass


def _stat_mtime_utc(path: Path) -> Optional[str]:
    try:
        ts = path.stat().st_mtime
    except OSError:
        return None
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _stat_mode(path: Path) -> Optional[int]:
    try:
        return path.stat().st_mode & 0o777
    except OSError:
        return None


# ---------------------------------------------------------------------------
# Subcommand implementations
# ---------------------------------------------------------------------------


def cmd_list(args: argparse.Namespace) -> int:
    profiles = _list_profiles()
    if not profiles:
        sys.stdout.write("(no profiles)\n")
        return 0
    current = _resolve_current()
    for name in profiles:
        marker = "*" if name == current else " "
        sys.stdout.write(f"{marker} {name}\n")
    return 0


def cmd_current(args: argparse.Namespace) -> int:
    if not LIVE_CRED.exists() and not LIVE_CRED.is_symlink():
        sys.stdout.write("missing\n")
        return 0
    if not LIVE_CRED.is_symlink():
        sys.stdout.write("unmanaged\n")
        return 0
    name = _resolve_current()
    if name is None:
        sys.stdout.write("unmanaged\n")
        return 0
    sys.stdout.write(f"{name}\n")
    return 0


def cmd_switch(args: argparse.Namespace) -> int:
    name = args.name
    try:
        _validate_profile_name(name)
    except ValueError as e:
        sys.stderr.write(f"metasphere accounts switch: {e}\n")
        return 2
    target = _profile_path(name)
    if not target.is_file():
        sys.stderr.write(
            f"metasphere accounts switch: profile '{name}' not found at {target}\n"
        )
        return 2
    _atomic_symlink(target, LIVE_CRED)
    sys.stdout.write(f"switched to {name}\n")
    return 0


def cmd_add(args: argparse.Namespace) -> int:
    name = args.name
    try:
        _validate_profile_name(name)
    except ValueError as e:
        sys.stderr.write(f"metasphere accounts add: {e}\n")
        return 2
    dest = _profile_path(name)
    if dest.exists() and not args.force:
        sys.stderr.write(
            f"metasphere accounts add: profile '{name}' already exists "
            f"(use --force to overwrite)\n"
        )
        return 2

    if args.from_path:
        source = Path(args.from_path).expanduser()
    else:
        # Snapshot the live credentials. If LIVE_CRED is a symlink,
        # ``open`` follows it; ``shutil.copyfile`` does the same — we
        # capture the target's contents, not the link.
        source = LIVE_CRED

    if not source.is_file():
        sys.stderr.write(
            f"metasphere accounts add: source not found: {source}\n"
        )
        return 2

    dest.parent.mkdir(parents=True, exist_ok=True)
    # Copy via temp + replace so an interrupted copy doesn't leave a
    # half-written credentials file in place.
    fd, tmp_name = tempfile.mkstemp(prefix=".cred-add-", dir=str(dest.parent))
    os.close(fd)
    try:
        shutil.copyfile(str(source), tmp_name)
        _enforce_mode(Path(tmp_name))
        os.replace(tmp_name, str(dest))
    except Exception:
        # Best-effort cleanup of the temp file; re-raise so the caller
        # sees the underlying error.
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    _enforce_mode(dest)
    sys.stdout.write(f"added profile '{name}' from {source}\n")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    current = _resolve_current()
    target_path: Optional[Path] = None
    target_exists = False
    if LIVE_CRED.is_symlink():
        try:
            target_path = LIVE_CRED.resolve(strict=False)
            target_exists = target_path.is_file()
        except OSError:
            target_path = None

    sys.stdout.write(f"current:        {current or 'unmanaged'}\n")
    sys.stdout.write(
        f"symlink:        {LIVE_CRED} -> "
        f"{target_path if target_path else '(none)'}\n"
    )
    sys.stdout.write(f"target exists:  {target_exists}\n")

    profiles = _list_profiles()
    if not profiles:
        sys.stdout.write("profiles:       (none)\n")
        return 0
    sys.stdout.write("profiles:\n")
    for name in profiles:
        path = _profile_path(name)
        mode = _stat_mode(path)
        mtime = _stat_mtime_utc(path) or "?"
        marker = "*" if name == current else " "
        mode_str = f"{mode:04o}" if mode is not None else "????"
        warn = "" if mode == SECURE_MODE else "  WARN: mode != 0600"
        sys.stdout.write(
            f"  {marker} {name:<16} mode={mode_str}  mtime={mtime}{warn}\n"
        )
    return 0


# ---------------------------------------------------------------------------
# Argparse + dispatch
# ---------------------------------------------------------------------------


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="metasphere accounts",
        description="Manage Anthropic OAuth credential profiles via symlink swap.",
    )
    sub = parser.add_subparsers(dest="cmd", metavar="{list,current,switch,add,status}")

    sub.add_parser("list", help="List profiles in ~/.metasphere/accounts/")
    sub.add_parser("current", help="Print the active profile name")

    p_switch = sub.add_parser(
        "switch", help="Atomically retarget the credentials symlink to <name>"
    )
    p_switch.add_argument("name", help="Profile name (must already exist)")

    p_add = sub.add_parser(
        "add",
        help="Capture a profile from --from <path> or snapshot the live file",
    )
    p_add.add_argument("name", help="Profile name to create")
    p_add.add_argument(
        "--from",
        dest="from_path",
        default=None,
        help="Source credentials.json (defaults to snapshotting the live file)",
    )
    p_add.add_argument(
        "--force",
        action="store_true",
        help="Overwrite an existing profile of the same name",
    )

    sub.add_parser("status", help="Show symlink integrity + per-profile mode/mtime")

    return parser


_HANDLERS = {
    "list": cmd_list,
    "current": cmd_current,
    "switch": cmd_switch,
    "add": cmd_add,
    "status": cmd_status,
}


def main(argv: Optional[list[str]] = None) -> int:
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list and args_list[0] in ("--help", "-h"):
        sys.stdout.write(USAGE)
        return 0
    rc = _refuse_on_darwin()
    if rc is not None:
        return rc

    parser = _build_parser()
    args = parser.parse_args(args_list)
    if not args.cmd:
        parser.print_help()
        return 0
    handler = _HANDLERS[args.cmd]
    return handler(args) or 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
