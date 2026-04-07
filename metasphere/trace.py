"""Command output capture (port of scripts/metasphere-trace).

Captures stdout/stderr/metadata for arbitrary commands into
``$METASPHERE_DIR/traces/YYYY-MM-DD/`` and appends each invocation to
``traces/index.jsonl``. Pure stdlib.
"""

from __future__ import annotations

import datetime as _dt
import json
import os
import re
import shlex
import shutil
import subprocess
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .identity import resolve_agent_id
from .io import append_jsonl, file_lock
from .paths import Paths, resolve

_ERROR_RE = re.compile(r"error|failed|exception|fatal|FAIL", re.IGNORECASE)


@dataclass
class Trace:
    id: str
    timestamp: str
    agent: str
    scope: str
    command: str
    exit_code: int
    duration_ms: int
    stdout_file: str
    stderr_file: str
    error_detected: bool = False
    error_type: str = ""
    error_summary: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _traces_dir(paths: Paths) -> Path:
    return paths.root / "traces"


def _slug(command: str) -> str:
    s = command.replace(" ", "-").replace("/", "-")
    return s[:30]


def _gen_id() -> str:
    return f"trace-{int(time.time())}-{os.getpid()}"


def _now_iso() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _scan_error(text: str) -> str:
    for line in text.splitlines():
        if _ERROR_RE.search(line):
            return line.strip()
    return ""


def capture_trace(
    command_argv: list[str] | str,
    *,
    agent: str | None = None,
    scope: str | None = None,
    paths: Paths | None = None,
    shell: bool | None = None,
) -> Trace:
    """Run ``command_argv`` capturing stdout/stderr to disk.

    If ``command_argv`` is a string, it is parsed with ``shlex.split``
    by default (no shell expansion). Pass ``shell=True`` explicitly to
    opt back into shell evaluation. Errors detected via exit code
    or common error keywords in stderr/stdout.
    """
    paths = paths or resolve()
    if isinstance(command_argv, str):
        cmd_str = command_argv
        run_shell = False if shell is None else shell
        run_arg: list[str] | str = command_argv if run_shell else shlex.split(command_argv)
    else:
        cmd_str = " ".join(command_argv)
        run_shell = False if shell is None else shell
        run_arg = list(command_argv)

    trace_id = _gen_id()
    now = _dt.datetime.now()
    date_dir = _traces_dir(paths) / now.strftime("%Y-%m-%d")
    date_dir.mkdir(parents=True, exist_ok=True)
    time_prefix = now.strftime("%H-%M-%S")
    base = f"{time_prefix}-{_slug(cmd_str)}"
    stdout_file = date_dir / f"{base}.stdout"
    stderr_file = date_dir / f"{base}.stderr"
    meta_file = date_dir / f"{base}.json"

    start = time.monotonic()
    with open(stdout_file, "wb") as out, open(stderr_file, "wb") as err:
        proc = subprocess.run(
            run_arg,
            shell=run_shell,
            stdout=out,
            stderr=err,
            check=False,
        )
    duration_ms = int((time.monotonic() - start) * 1000)
    exit_code = proc.returncode

    err_text = stderr_file.read_text(encoding="utf-8", errors="replace")
    out_text = stdout_file.read_text(encoding="utf-8", errors="replace")

    error_detected = False
    error_type = ""
    error_summary = ""
    if exit_code != 0:
        error_detected = True
        error_type = "exit_code"
        error_summary = f"Command exited with code {exit_code}"
    err_match = _scan_error(err_text)
    if err_match:
        error_detected = True
        if not error_type:
            error_type = "stderr_error"
        error_summary = err_match
    elif not error_summary:
        out_match = _scan_error(out_text)
        if out_match:
            error_detected = True
            if not error_type:
                error_type = "stdout_error"
            error_summary = out_match

    trace = Trace(
        id=trace_id,
        timestamp=_now_iso(),
        agent=agent or resolve_agent_id(paths),
        scope=scope or str(paths.scope),
        command=cmd_str,
        exit_code=exit_code,
        duration_ms=duration_ms,
        stdout_file=str(stdout_file),
        stderr_file=str(stderr_file),
        error_detected=error_detected,
        error_type=error_type,
        error_summary=error_summary,
    )

    meta_file.write_text(json.dumps(trace.to_dict(), indent=2) + "\n")
    append_jsonl(_traces_dir(paths) / "index.jsonl", trace.to_dict())
    return trace


def list_traces(
    *,
    limit: int = 20,
    errors_only: bool = False,
    paths: Paths | None = None,
) -> list[Trace]:
    paths = paths or resolve()
    index = _traces_dir(paths) / "index.jsonl"
    if not index.exists():
        return []
    out: list[Trace] = []
    with file_lock(index, exclusive=False):
        lines = index.read_text(encoding="utf-8").splitlines()
    for line in lines:
        if not line.strip():
            continue
        try:
            d = json.loads(line)
        except json.JSONDecodeError:
            continue
        if errors_only and not d.get("error_detected"):
            continue
        out.append(Trace(**{k: d.get(k) for k in Trace.__dataclass_fields__}))
    return out[-limit:]


def search_traces(
    pattern: str,
    *,
    paths: Paths | None = None,
    limit: int = 50,
) -> list[Trace]:
    """Stream the trace index line-by-line and short-circuit at ``limit``.

    M1 (wave-4 review): the previous implementation read up to 10 000 rows
    into memory before filtering, silently truncating long histories. This
    version reads `index.jsonl` line-at-a-time under a shared lock and
    stops as soon as ``limit`` matches are collected.
    """
    paths = paths or resolve()
    rx = re.compile(pattern, re.IGNORECASE)
    index = _traces_dir(paths) / "index.jsonl"
    if not index.exists():
        return []
    out: list[Trace] = []
    with file_lock(index, exclusive=False):
        with index.open("r", encoding="utf-8") as f:
            for line in f:
                if not line.strip():
                    continue
                try:
                    d = json.loads(line)
                except json.JSONDecodeError:
                    continue
                cmd = d.get("command", "") or ""
                summary = d.get("error_summary", "") or ""
                if rx.search(cmd) or rx.search(summary):
                    out.append(Trace(**{k: d.get(k) for k in Trace.__dataclass_fields__}))
                    if len(out) >= limit:
                        break
    return out


def prune_traces(older_than_days: int, *, paths: Paths | None = None) -> int:
    """Delete trace dated subdirs older than ``older_than_days``.

    Returns count of directories removed.
    """
    paths = paths or resolve()
    base = _traces_dir(paths)
    if not base.exists():
        return 0
    cutoff = _dt.date.today() - _dt.timedelta(days=older_than_days)
    removed = 0
    for child in base.iterdir():
        if not child.is_dir():
            continue
        try:
            d = _dt.date.fromisoformat(child.name)
        except ValueError:
            continue
        if d < cutoff:
            shutil.rmtree(child)
            removed += 1
    return removed
