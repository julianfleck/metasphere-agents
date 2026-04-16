"""Configurable auto-updates for metasphere hosts.

Hosts running metasphere (spot, bean, future) keep themselves current
with main without manual intervention. This module owns:

* parsing ``$METASPHERE_DIR/config/auto-update.env``
* registering / unregistering the cron job in
  ``$METASPHERE_DIR/schedule/jobs.json``
* the actual update flow (git pull / claude integration / daemon restart
  / re-pip-install / tests / telegram notify)
* status / enable / disable wrappers driven by the CLI

All subprocess shelling is contained in module-level helpers so tests can
monkeypatch them. The legacy ``scripts/metasphere update`` bash path was
retired; see git history if you need the shell version.
"""

from __future__ import annotations

import dataclasses
import datetime as _dt
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from . import schedule as _sched
from .paths import Paths, resolve

logger = logging.getLogger(__name__)


# ---------- config ----------

CONFIG_FILENAME = "auto-update.env"
LOG_FILENAME = "auto-update.log"
STATE_FILENAME = "auto-update.state.json"
JOB_ID = "metasphere-auto-update"
JOB_NAME = "metasphere:auto-update"

INTERVAL_TO_CRON = {
    "daily": "0 4 * * *",
    "hourly": "0 * * * *",
    "6h": "0 */6 * * *",
}


@dataclass
class AutoUpdateConfig:
    enabled: bool = False
    interval: str = "daily"
    branch: str = "main"
    restart_daemons: bool = True
    notify: bool = True

    def cron_expr(self) -> str:
        return interval_to_cron(self.interval)

    def to_env_text(self) -> str:
        return (
            "# metasphere auto-update configuration\n"
            "# Managed by `metasphere update --enable|--disable`.\n"
            f"AUTO_UPDATE_ENABLED={'true' if self.enabled else 'false'}\n"
            f"AUTO_UPDATE_INTERVAL={self.interval}\n"
            f"AUTO_UPDATE_BRANCH={self.branch}\n"
            f"AUTO_UPDATE_RESTART_DAEMONS={'true' if self.restart_daemons else 'false'}\n"
            f"AUTO_UPDATE_NOTIFY={'true' if self.notify else 'false'}\n"
        )


def _truthy(v: str) -> bool:
    return v.strip().lower() in ("1", "true", "yes", "on", "y")


def parse_env_text(text: str) -> AutoUpdateConfig:
    """Parse the simple ``KEY=VALUE`` format. Unknown keys ignored."""
    cfg = AutoUpdateConfig()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k == "AUTO_UPDATE_ENABLED":
            cfg.enabled = _truthy(v)
        elif k == "AUTO_UPDATE_INTERVAL":
            cfg.interval = v or "daily"
        elif k == "AUTO_UPDATE_BRANCH":
            cfg.branch = v or "main"
        elif k == "AUTO_UPDATE_RESTART_DAEMONS":
            cfg.restart_daemons = _truthy(v)
        elif k == "AUTO_UPDATE_NOTIFY":
            cfg.notify = _truthy(v)
    return cfg


def config_path(paths: Paths | None = None) -> Path:
    paths = paths or resolve()
    return paths.config / CONFIG_FILENAME


def log_path(paths: Paths | None = None) -> Path:
    paths = paths or resolve()
    return paths.logs / LOG_FILENAME


def state_path(paths: Paths | None = None) -> Path:
    paths = paths or resolve()
    return paths.state / STATE_FILENAME


def load_config(paths: Paths | None = None) -> AutoUpdateConfig:
    p = config_path(paths)
    if not p.exists():
        return AutoUpdateConfig()
    return parse_env_text(p.read_text(encoding="utf-8"))


def save_config(cfg: AutoUpdateConfig, paths: Paths | None = None) -> Path:
    p = config_path(paths)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(cfg.to_env_text(), encoding="utf-8")
    try:
        os.chmod(p, 0o600)
    except OSError:  # pragma: no cover - non-fatal on weird FS
        pass
    return p


# ---------- cron expression ----------

_CRON_FIELD_RE = ("*/", "-", ",", "*")


def looks_like_cron(s: str) -> bool:
    parts = s.strip().split()
    if len(parts) != 5:
        return False
    return any(any(tok in p for tok in _CRON_FIELD_RE) or p.isdigit() for p in parts)


def interval_to_cron(interval: str) -> str:
    """Map an interval keyword to a cron expression.

    If ``interval`` already looks like a 5-field cron expression, return
    it verbatim (allows ``AUTO_UPDATE_INTERVAL='*/15 * * * *'`` for tests
    and staging).
    """
    if interval in INTERVAL_TO_CRON:
        return INTERVAL_TO_CRON[interval]
    if looks_like_cron(interval):
        return interval
    return INTERVAL_TO_CRON["daily"]


# ---------- schedule integration ----------

def _metasphere_binary() -> str:
    """Absolute path to the ``metasphere`` console script in the venv we're
    running under. Using an absolute path in the cron payload avoids
    relying on the systemd unit's PATH (which may resolve ``metasphere``
    to a bash shim backed by ``/usr/bin/python3``, which in turn can't
    import the pip-installed ``metasphere`` package and fails silently
    with ``ModuleNotFoundError``).
    """
    candidate = Path(sys.executable).with_name("metasphere")
    if candidate.is_file():
        return str(candidate)
    return "metasphere"  # last-resort fallback


def build_job(cfg: AutoUpdateConfig) -> _sched.Job:
    """Construct the auto-update Job for jobs.json."""
    cmd = f"{_metasphere_binary()} update --quiet"
    return _sched.Job(
        id=JOB_ID,
        source="auto-update",
        source_id=JOB_ID,
        agent_id="auto-update",
        name=JOB_NAME,
        enabled=cfg.enabled,
        kind="cron",
        cron_expr=cfg.cron_expr(),
        tz="UTC",
        payload_kind="command",
        payload_message=cmd,
        imported_at=int(time.time()),
        command=cmd,
        full_command=cmd,
    )


def register_job(cfg: AutoUpdateConfig, paths: Paths | None = None) -> _sched.Job:
    """Idempotently install/refresh the auto-update job from ``cfg``.

    Adds the job if missing, updates ``cron_expr`` and ``enabled`` if
    present. Returns the persisted Job.
    """
    paths = paths or resolve()
    paths.schedule.mkdir(parents=True, exist_ok=True)
    new_job = build_job(cfg)
    with _sched.with_locked_jobs(paths) as jobs:
        input_count = len(jobs)
        replaced = False
        for i, j in enumerate(jobs):
            if j.id == JOB_ID:
                # Preserve last_fired_at across re-registration so we
                # don't double-fire after a reinstall.
                new_job.last_fired_at = j.last_fired_at
                jobs[i] = new_job
                replaced = True
                break
        if not replaced:
            jobs.append(new_job)
        _sched.save_jobs(jobs, paths, _input_count=input_count)
    return new_job


def unregister_job(paths: Paths | None = None) -> bool:
    paths = paths or resolve()
    if not paths.schedule_jobs.exists():
        return False
    with _sched.with_locked_jobs(paths) as jobs:
        input_count = len(jobs)
        kept = [j for j in jobs if j.id != JOB_ID]
        if len(kept) == input_count:
            return False
        # Shrink-detection: only refuses 0-out-of-N. We have N-1 here so
        # save is allowed even when N==1 (kept=[] and input_count=1
        # would trip the guard) — handle that case explicitly.
        if not kept and input_count > 0:
            # Write directly past the shrink guard (intentional removal).
            paths.schedule_jobs.write_text("[]\n", encoding="utf-8")
            return True
        _sched.save_jobs(kept, paths, _input_count=input_count)
    return True


# ---------- state ----------

def load_state(paths: Paths | None = None) -> dict:
    p = state_path(paths)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8") or "{}")
    except (OSError, json.JSONDecodeError):
        return {}


def save_state(state: dict, paths: Paths | None = None) -> None:
    p = state_path(paths)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# ---------- update flow ----------

@dataclass
class UpdateResult:
    ok: bool
    old_hash: str = ""
    new_hash: str = ""
    commits: int = 0
    subjects: list[str] = dataclasses.field(default_factory=list)
    reason: str = ""
    pip_reinstalled: bool = False
    tests_passed: Optional[bool] = None
    daemons_restarted: bool = False

    def to_dict(self) -> dict:
        d = dataclasses.asdict(self)
        return d


GitRunner = Callable[[list[str]], "subprocess.CompletedProcess[str]"]


def _git(repo: Path) -> GitRunner:
    def run(args: list[str]) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", "-C", str(repo), *args],
            check=False,
            capture_output=True,
            text=True,
            timeout=120,
        )
    return run


def _head_hash(repo: Path, runner: GitRunner | None = None) -> str:
    g = runner or _git(repo)
    r = g(["rev-parse", "HEAD"])
    return (r.stdout or "").strip()


def _commit_subjects(repo: Path, old: str, new: str, runner: GitRunner | None = None) -> list[str]:
    if not old or not new or old == new:
        return []
    g = runner or _git(repo)
    r = g(["log", "--pretty=%s", f"{old}..{new}"])
    return [line for line in (r.stdout or "").splitlines() if line]


def _has_python_changes(repo: Path, old: str, new: str, runner: GitRunner | None = None) -> bool:
    if not old or not new or old == new:
        return False
    g = runner or _git(repo)
    r = g(["diff", "--name-only", f"{old}..{new}"])
    for f in (r.stdout or "").splitlines():
        if f == "pyproject.toml" or f.startswith("metasphere/"):
            return True
    return False


def _venv_python() -> Path | None:
    """Best-effort: find the venv that owns the running python."""
    exe = Path(sys.executable)
    return exe if exe.exists() else None


def _logf(paths: Paths) -> Path:
    p = log_path(paths)
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _append_log(paths: Paths, line: str) -> None:
    ts = _dt.datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    with _logf(paths).open("a", encoding="utf-8") as fh:
        fh.write(f"[{ts}] {line}\n")


def _dirty_paths(runner: GitRunner) -> list[str]:
    """Return the list of dirty porcelain lines from ``git status``.

    Empty list = clean tree. Each element is a raw ``XY path`` line from
    ``git status --porcelain``, so callers can both count (truthiness)
    and render a useful error message.
    """
    r = runner(["status", "--porcelain"])
    if r.returncode != 0:
        # Fail closed on an unusable status call — treat "can't tell"
        # as "dirty" rather than silently proceeding to reset --hard.
        return [f"(git status failed rc={r.returncode})"]
    return [line for line in (r.stdout or "").splitlines() if line.strip()]


def _git_pull_or_reset(repo: Path, branch: str, runner: GitRunner) -> None:
    """Fast-forward ``repo`` to ``origin/<branch>`` with a hard-reset fallback.

    Refuses to proceed if the working tree has uncommitted changes —
    the fallback is ``git reset --hard``, which silently destroys WIP.
    Bit Julian on 2026-04-16 (10 files of uncommitted tmux work erased
    by a wake-triggered auto-update). Caller must commit, stash, or
    explicitly discard before re-running.

    Mirrors the bash ``git pull --ff-only`` → ``git fetch && git reset --hard``
    chain from the retired ``scripts/metasphere update`` path. Raises
    ``RuntimeError`` if the tree is dirty or if both strategies fail.
    """
    dirty = _dirty_paths(runner)
    if dirty:
        preview = "\n  ".join(dirty[:20])
        more = f"\n  ...and {len(dirty) - 20} more" if len(dirty) > 20 else ""
        raise RuntimeError(
            "refusing to update: working tree has uncommitted changes.\n"
            "The reset --hard fallback would silently destroy them.\n"
            "Commit, stash, or `git checkout -- .` before re-running.\n"
            f"Dirty paths:\n  {preview}{more}"
        )
    ff = runner(["pull", "--ff-only", "origin", branch])
    if ff.returncode == 0:
        return
    logger.warning("git pull --ff-only failed (rc=%s), retrying via fetch+reset", ff.returncode)
    fetch = runner(["fetch", "origin"])
    if fetch.returncode != 0:
        raise RuntimeError(
            f"git fetch origin failed (rc={fetch.returncode}): "
            f"{(fetch.stderr or fetch.stdout or '').strip()}"
        )
    reset = runner(["reset", "--hard", f"origin/{branch}"])
    if reset.returncode != 0:
        raise RuntimeError(
            f"git reset --hard origin/{branch} failed (rc={reset.returncode}): "
            f"{(reset.stderr or reset.stdout or '').strip()}"
        )


def _sync_claude_integration(repo: Path, home_dir: Path) -> None:
    """Refresh ``~/.claude/{skills,commands}`` symlinks from the repo.

    * Each ``skills/<name>/`` containing ``SKILL.md`` is linked into
      ``<home>/.claude/skills/<name>``. A pre-existing real directory
      (not a symlink) with a ``.user-customized`` marker is left alone.
    * Each ``.claude/commands/*.md`` is linked into
      ``<home>/.claude/commands/<basename>``.
    """
    # Skills
    src_skills = repo / "skills"
    if src_skills.is_dir():
        dst_skills = home_dir / ".claude" / "skills"
        dst_skills.mkdir(parents=True, exist_ok=True)
        for child in sorted(src_skills.iterdir()):
            if not child.is_dir():
                continue
            if not (child / "SKILL.md").is_file():
                continue
            name = child.name
            dst = dst_skills / name
            if (
                dst.exists()
                and not dst.is_symlink()
                and dst.is_dir()
                and (dst / ".user-customized").is_file()
            ):
                continue
            # Replace any existing symlink / file; leave customized real dirs alone.
            if dst.is_symlink() or dst.is_file():
                try:
                    dst.unlink()
                except OSError:
                    pass
            elif dst.exists() and dst.is_dir():
                # A real directory without .user-customized — leave it alone
                # rather than rm -rf (conservative; matches bash behaviour,
                # which used ln -sfn that fails silently against real dirs).
                continue
            try:
                os.symlink(child.resolve(), dst)
            except OSError as e:
                logger.info("skills symlink skipped for %s: %s", name, e)

    # Commands
    src_cmds = repo / ".claude" / "commands"
    if src_cmds.is_dir():
        dst_cmds = home_dir / ".claude" / "commands"
        dst_cmds.mkdir(parents=True, exist_ok=True)
        for md in sorted(src_cmds.glob("*.md")):
            if not md.is_file():
                continue
            dst = dst_cmds / md.name
            if dst.is_symlink() or dst.is_file():
                try:
                    dst.unlink()
                except OSError:
                    pass
            try:
                os.symlink(md.resolve(), dst)
            except OSError as e:
                logger.info("command symlink skipped for %s: %s", md.name, e)


def _restart_daemons() -> None:
    """Restart the metasphere gateway/daemon after an update.

    Defensive: if the platform's service manager or unit isn't present,
    this function logs and returns without raising.
    """
    system = platform.system()
    if system == "Darwin":
        plist = Path.home() / "Library" / "LaunchAgents" / "com.metasphere.plist"
        if not plist.is_file() or not shutil.which("launchctl"):
            logger.info("launchctl/plist not present; skipping daemon restart")
            return
        subprocess.run(["launchctl", "unload", str(plist)], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        subprocess.run(["launchctl", "load", str(plist)], check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        return

    # Linux (systemd --user)
    if not shutil.which("systemctl"):
        logger.info("systemctl not found; skipping daemon restart")
        return

    def _sc(*args: str) -> int:
        proc = subprocess.run(
            ["systemctl", "--user", *args],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return proc.returncode

    # Gateway owns polling now — stop+disable stale standalone pollers.
    for stale in ("metasphere-telegram.service", "metasphere-telegram-stream.service"):
        if _sc("is-enabled", stale) == 0:
            _sc("stop", stale)
            _sc("disable", stale)

    if _sc("is-active", "metasphere-gateway") == 0:
        _sc("restart", "metasphere-gateway")
    elif _sc("is-active", "metasphere") == 0:
        _sc("restart", "metasphere")


def notify(text: str, *, sender: Callable[[str], None] | None = None) -> None:
    """Send a notification line. Default sender uses telegram.api if a
    chat is configured; otherwise this is a no-op. Tests inject ``sender``.
    """
    if sender is not None:
        try:
            sender(text)
        except Exception as e:  # pragma: no cover - defensive
            logger.warning("notify sender failed: %s", e)
        return
    try:
        from .telegram.groups import send_to_topic  # type: ignore
        send_to_topic("orchestrator", text)
    except Exception as e:
        logger.info("auto-update notify suppressed: %s", e)


def run_update(
    *,
    paths: Paths | None = None,
    cfg: AutoUpdateConfig | None = None,
    quiet: bool = False,
    git_runner: GitRunner | None = None,
    pip_runner: Callable[[list[str]], int] | None = None,
    test_runner: Callable[[], bool] | None = None,
    notify_sender: Callable[[str], None] | None = None,
) -> UpdateResult:
    """Run a one-shot auto-update.

    All side-effecting subprocesses are injectable so tests can monkeypatch
    them. The git pull, claude-skills/commands symlink sync, and daemon
    restart steps are handled by the module-level helpers
    :func:`_git_pull_or_reset`, :func:`_sync_claude_integration`, and
    :func:`_restart_daemons` (monkeypatch those directly in tests).
    """
    paths = paths or resolve()
    cfg = cfg or load_config(paths)
    repo = paths.project_root
    runner = git_runner or _git(repo)

    def log(line: str) -> None:
        if quiet:
            _append_log(paths, line)
        else:
            print(line)
            _append_log(paths, line)

    log(f"auto-update: starting (branch={cfg.branch}, restart={cfg.restart_daemons})")

    old = _head_hash(repo, runner)
    log(f"auto-update: HEAD before: {old or '(unknown)'}")

    # 1. Git pull (with fetch+reset fallback).
    try:
        _git_pull_or_reset(repo, cfg.branch, runner)
    except Exception as e:
        reason = f"git pull failed: {e}"
        log(f"auto-update: FAILED — {reason}")
        result = UpdateResult(ok=False, old_hash=old, reason=reason)
        _record_result(paths, result, cfg, notify_sender)
        return result

    # 2. Refresh ~/.claude/{skills,commands} symlinks.
    try:
        _sync_claude_integration(repo, Path.home())
    except Exception as e:
        # Non-fatal: log and continue. The update itself succeeded; a
        # skills-sync failure shouldn't block pip reinstall / daemon restart.
        log(f"auto-update: claude integration sync warning: {e}")

    # 3. Daemon restart (skippable via cfg.restart_daemons).
    if cfg.restart_daemons:
        try:
            _restart_daemons()
        except Exception as e:
            log(f"auto-update: daemon restart warning: {e}")

    new = _head_hash(repo, runner)
    log(f"auto-update: HEAD after: {new or '(unknown)'}")

    subjects = _commit_subjects(repo, old, new, runner)
    pip_reinstalled = False
    if _has_python_changes(repo, old, new, runner):
        log("auto-update: python changes detected, re-installing package")
        # --no-warn-script-location: pip prints a noisy warning when
        # console_scripts land in a user-site bin dir that isn't on
        # PATH. For metasphere that's expected — only the symlinked
        # `metasphere` in `$METASPHERE_DIR/bin` needs to be on PATH
        # (install.sh sets that up); the other pip-installed scripts
        # are harmless.
        #
        # --break-system-packages: PEP 668 escape hatch for Debian 12+
        # / Python 3.12+ hosts where the system Python ships with an
        # EXTERNALLY-MANAGED marker. Without this flag, the reinstall
        # fails with "error: externally-managed-environment". This
        # flag affects only the pip install path pip has already
        # chosen (user-site under ``~/.local/`` when not in a venv)
        # — it does NOT let us overwrite apt-managed packages.
        #
        # Proper long-term fix is a dedicated venv at
        # ``$METASPHERE_DIR/venv``; tracked as a follow-up task.
        rc = (pip_runner or _default_pip_runner)([
            "-m", "pip", "install", "-e", str(repo), "--quiet",
            "--no-warn-script-location",
            "--break-system-packages",
        ])
        if rc != 0:
            reason = f"pip install -e exited rc={rc}"
            log(f"auto-update: FAILED — {reason}")
            result = UpdateResult(
                ok=False, old_hash=old, new_hash=new, commits=len(subjects),
                subjects=subjects, reason=reason,
            )
            _record_result(paths, result, cfg, notify_sender)
            return result
        pip_reinstalled = True

    tests_passed: Optional[bool] = None
    if test_runner is not None:
        log("auto-update: running test gate (pytest -m 'not live' -q)")
        try:
            tests_passed = bool(test_runner())
        except Exception as e:
            tests_passed = False
            log(f"auto-update: test runner raised: {e}")
        if not tests_passed:
            reason = "test gate failed"
            log(f"auto-update: FAILED — {reason}")
            result = UpdateResult(
                ok=False, old_hash=old, new_hash=new, commits=len(subjects),
                subjects=subjects, reason=reason,
                pip_reinstalled=pip_reinstalled, tests_passed=False,
            )
            _record_result(paths, result, cfg, notify_sender)
            return result

    result = UpdateResult(
        ok=True,
        old_hash=old,
        new_hash=new,
        commits=len(subjects),
        subjects=subjects,
        pip_reinstalled=pip_reinstalled,
        tests_passed=tests_passed,
        daemons_restarted=cfg.restart_daemons,
    )
    log(f"auto-update: ok ({len(subjects)} commits applied)")
    _record_result(paths, result, cfg, notify_sender)
    return result


def _default_pip_runner(args: list[str]) -> int:
    return subprocess.call([sys.executable, *args])


def _record_result(
    paths: Paths,
    result: UpdateResult,
    cfg: AutoUpdateConfig,
    sender: Callable[[str], None] | None,
) -> None:
    state = load_state(paths)
    state["last_run_at"] = int(time.time())
    state["last_result"] = result.to_dict()
    save_state(state, paths)

    if not cfg.notify:
        return
    host = os.uname().nodename
    if result.ok:
        if result.commits == 0:
            return  # nothing changed; don't ping
        head = (result.subjects[:3] + ["…"]) if len(result.subjects) > 3 else result.subjects
        msg = (
            f"auto-update: {host} now at {result.new_hash[:10]}, "
            f"was {result.old_hash[:10]}, {result.commits} commits applied: "
            + "; ".join(head)
        )
    else:
        msg = (
            f"auto-update FAILED on {host}: {result.reason}, "
            f"last good {result.old_hash[:10] or 'unknown'}, "
            f"daemons NOT restarted"
        )
    notify(msg, sender=sender)


# ---------- status ----------

def status_text(paths: Paths | None = None) -> str:
    paths = paths or resolve()
    cfg = load_config(paths)
    state = load_state(paths)
    lines = [
        "metasphere auto-update",
        f"  enabled:         {cfg.enabled}",
        f"  interval:        {cfg.interval}  ({cfg.cron_expr()})",
        f"  branch:          {cfg.branch}",
        f"  restart_daemons: {cfg.restart_daemons}",
        f"  notify:          {cfg.notify}",
        f"  config:          {config_path(paths)}",
        f"  log:             {log_path(paths)}",
    ]
    last = state.get("last_run_at")
    if last:
        lines.append(
            f"  last run:        {_dt.datetime.fromtimestamp(int(last)).strftime('%Y-%m-%d %H:%M:%S')}"
        )
    last_result = state.get("last_result") or {}
    if last_result:
        lines.append(f"  last result:     ok={last_result.get('ok')} commits={last_result.get('commits', 0)}")
        if last_result.get("reason"):
            lines.append(f"  last reason:     {last_result['reason']}")
    # job presence
    try:
        jobs = _sched.load_jobs(paths)
        job = next((j for j in jobs if j.id == JOB_ID), None)
        if job:
            lines.append(f"  cron job:        {job.cron_expr} (enabled={job.enabled})")
        else:
            lines.append("  cron job:        (not registered)")
    except Exception as e:  # pragma: no cover - defensive
        lines.append(f"  cron job:        (error: {e})")
    return "\n".join(lines) + "\n"
