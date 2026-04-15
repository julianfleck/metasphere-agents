"""``metasphere audit-docs`` — scan commits-since-last-CHANGELOG for doc drift.

Per-project audit: read the project's ``CHANGELOG.md``, find the date
of the newest entry, run ``git log --since=<date>`` in the registered
repo, classify the commits, and emit a draft CHANGELOG stanza plus a
README-staleness flag list.

Intended to run from cron (see :mod:`metasphere.cli.audit_docs
.register_cron`). The output is a markdown report; auto-PR creation
is deliberately out of scope for this first cut — operators file the
PR manually after reviewing the draft. Follow-up PR can wire the
ephemeral-spawn + auto-PR flow on top of this CLI.

Exit codes:
    0    report produced (nothing staleness-flagged)
    1    report produced AND README-staleness flags raised
    2    precondition failed (unknown project, no repo, etc)
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Iterable, List, Optional

from metasphere import project as _project
from metasphere.paths import Paths, resolve


#: README-staleness signals in commit messages. Any commit whose subject
#: line matches one of these → the README likely needs an update, which
#: means the audit should surface it to a human (not silently append to
#: CHANGELOG and call it done).
_STALE_KEYWORDS = (
    "cli", "subcommand", "command", "syntax",
    "schema", "migration", "migrate",
    "architecture", "canonical", "layout",
    "rename", "deprecate", "remove",
)

#: File-path globs that indicate a commit touched surfaces the README
#: documents. A commit touching one of these with no corresponding
#: README update is a potential staleness flag.
_STALE_PATH_PATTERNS = (
    "metasphere/cli/",
    "BOT_COMMANDS_MANIFEST",
    "project.py",
    "schedule/jobs",
)

#: Default output dir for audit reports. One file per audit run,
#: namespaced by project + date.
REPORTS_ROOT = Path.home() / ".metasphere" / "audits"


def _changelog_newest_date(changelog: Path) -> Optional[str]:
    """Extract the newest ISO date from a CHANGELOG.md. Looks for lines
    starting with ``## `` that contain either a bracketed ISO timestamp
    (``## [2026-04-15T...]``) or a bare ISO date (``## 2026-04-15``).
    Returns ``YYYY-MM-DD`` (oldest-sufficient since git ``--since`` is
    day-granular) or ``None`` if no date found.
    """
    if not changelog.is_file():
        return None
    try:
        text = changelog.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    #: Accept both ``## [2026-04-15T...]`` and ``## 2026-04-15 — foo``.
    date_re = re.compile(r"^##\s+\[?(\d{4}-\d{2}-\d{2})")
    for line in text.splitlines():
        m = date_re.match(line)
        if m:
            return m.group(1)
    return None


def _git_log_since(repo: Path, since: str) -> List[dict]:
    """Parse ``git log --since=<date> --name-only`` into a list of
    ``{"sha", "subject", "files"}`` records. Empty list on any git
    failure.

    ``git log --pretty=format:%H|%s --name-only`` emits one commit
    per block: a ``<sha>|<subject>`` header line, zero-or-more file
    paths (one per line), then a blank-line separator between commits.
    """
    if not (repo / ".git").is_dir():
        return []
    try:
        out = subprocess.run(
            ["git", "-C", str(repo), "log",
             f"--since={since}",
             "--pretty=format:%H|%s",
             "--name-only"],
            check=False, text=True, capture_output=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    records: List[dict] = []
    current: Optional[dict] = None
    for line in out.splitlines():
        if not line:
            # Blank line separates commits. If we have a commit in
            # flight, flush it; otherwise ignore.
            if current is not None:
                records.append(current)
                current = None
            continue
        if current is None or "|" in line and line.split("|", 1)[0].isalnum() \
                and len(line.split("|", 1)[0]) == 40:
            # New commit header: <40-char sha>|<subject>.
            if current is not None:
                records.append(current)
            sha, _, subject = line.partition("|")
            current = {"sha": sha, "subject": subject, "files": []}
        else:
            current["files"].append(line)
    if current is not None:
        records.append(current)
    return records


def _classify_subject(subject: str) -> str:
    """Conventional-commit-ish type. Returns ``feat`` / ``fix`` /
    ``refactor`` / ``docs`` / ``chore`` / ``other``. Robust to the
    ``type(scope):`` form used in this repo.
    """
    s = subject.strip().lower()
    for kind in ("feat", "fix", "refactor", "docs", "chore", "port", "test"):
        if s.startswith(kind + ":") or s.startswith(kind + "("):
            return kind
    return "other"


def _staleness_flags(records: List[dict]) -> List[str]:
    """Return human-readable flags for commits that likely invalidate
    README / doc content. One flag per affected commit.
    """
    flags: List[str] = []
    for rec in records:
        subject_l = rec["subject"].lower()
        files = rec.get("files", []) or []
        by_keyword = [kw for kw in _STALE_KEYWORDS if kw in subject_l]
        by_path = [
            p for p in files
            if any(pat in p for pat in _STALE_PATH_PATTERNS)
        ]
        if by_keyword or by_path:
            short = rec["sha"][:7]
            reasons = ", ".join(by_keyword + by_path[:2])
            flags.append(f"{short} {rec['subject'][:70]} ({reasons})")
    return flags


def _render_changelog_draft(project_name: str, since: str,
                             records: List[dict]) -> str:
    """Produce a markdown stanza suitable for appending to the
    project's CHANGELOG.md. Groups commits by conventional type.
    """
    today = _dt.date.today().isoformat()
    buckets: dict[str, list[dict]] = {}
    for rec in records:
        buckets.setdefault(_classify_subject(rec["subject"]), []).append(rec)

    lines = [
        f"## {today} — audit draft ({project_name})",
        "",
        f"_Since {since} — {len(records)} commits._",
        "",
    ]
    order = ("feat", "fix", "refactor", "port", "docs", "test", "chore", "other")
    titles = {
        "feat": "New features", "fix": "Fixes", "refactor": "Refactors",
        "port": "Ports", "docs": "Docs", "test": "Tests",
        "chore": "Chores", "other": "Other",
    }
    for kind in order:
        entries = buckets.get(kind, [])
        if not entries:
            continue
        lines.append(f"### {titles[kind]}")
        lines.append("")
        for rec in entries[:20]:  # cap per bucket so runaway churn doesn't explode the report
            lines.append(f"- `{rec['sha'][:7]}` {rec['subject']}")
        if len(entries) > 20:
            lines.append(f"- … and {len(entries) - 20} more")
        lines.append("")
    return "\n".join(lines)


def _render_report(project_name: str, since: str,
                    records: List[dict], stale: List[str]) -> str:
    parts: List[str] = [
        f"# Doc audit — {project_name}",
        "",
        f"Repo scanned: commits since `{since}`.",
        "",
    ]
    if not records:
        parts.append("**No new commits.** Nothing to audit.")
        return "\n".join(parts) + "\n"
    parts.append(f"**{len(records)} commit(s)** since the last CHANGELOG entry.")
    parts.append("")
    if stale:
        parts.append(f"## README staleness flags ({len(stale)})")
        parts.append("")
        parts.append(
            "These commits touched CLI / schema / architecture surfaces. "
            "Review the README before shipping the CHANGELOG draft below."
        )
        parts.append("")
        for f in stale:
            parts.append(f"- {f}")
        parts.append("")
    else:
        parts.append("_No README staleness flags raised._")
        parts.append("")
    parts.append("## CHANGELOG draft")
    parts.append("")
    parts.append(_render_changelog_draft(project_name, since, records))
    return "\n".join(parts) + "\n"


def _notify_orchestrator(project_name: str, report_path: Path,
                          stale_count: int, *,
                          sender=None) -> None:
    """Send an ``!info`` to ``@orchestrator`` so a human sees the report.

    Best-effort: message-send failures don't mask the report itself.
    """
    try:
        from ..messages import send_message as _send
    except Exception:  # noqa: BLE001
        return
    sender = sender or _send
    body = (
        f"doc audit: {project_name} — {stale_count} README-staleness flag(s). "
        f"Report: {report_path}"
    )
    try:
        sender(
            target="@orchestrator",
            label="!info",
            body=body,
            from_agent="@audit-docs",
            wake=False,
        )
    except Exception:  # noqa: BLE001
        pass


def _run_audit(project_name: str, *, paths: Paths,
                output_dir: Optional[Path] = None,
                notify: bool = True) -> tuple[int, Path]:
    """Execute an audit for one project.

    Returns ``(exit_code, report_path)``. Exit codes:
      * 0 — report produced, no staleness flags
      * 1 — report produced, staleness flags raised
      * 2 — precondition failure (no such project, no repo)
    """
    proj = _project.Project.for_name(project_name, paths)
    if proj is None:
        print(f"audit-docs: unknown project: {project_name}", file=sys.stderr)
        return 2, Path()
    repo = Path(proj.path)
    if not repo.is_dir():
        print(f"audit-docs: project path does not exist: {repo}",
              file=sys.stderr)
        return 2, Path()

    changelog = repo / "CHANGELOG.md"
    since = _changelog_newest_date(changelog)
    if since is None:
        # No CHANGELOG or no datable entries — audit the last 7 days.
        since = (_dt.date.today() - _dt.timedelta(days=7)).isoformat()

    records = _git_log_since(repo, since)
    stale = _staleness_flags(records)
    report = _render_report(project_name, since, records, stale)

    out_dir = (output_dir or REPORTS_ROOT) / _dt.date.today().isoformat()
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / f"{project_name}.md"
    out_path.write_text(report, encoding="utf-8")

    if notify and stale:
        _notify_orchestrator(project_name, out_path, len(stale))

    return (1 if stale else 0), out_path


#: Default cron expression for the daily audit. 18:00 local per
#: @orchestrator's brief. Operators who want a different slot edit
#: ``jobs.json`` or pass ``--cron-expr`` to ``register-cron``.
_DEFAULT_CRON_EXPR = "0 18 * * *"


def _audit_job_id(project_name: str) -> str:
    return f"audit-docs:{project_name}"


def _metasphere_bin() -> str:
    """Best-effort locate a ``metasphere`` binary for the cron command.

    Falls back to the literal ``metasphere`` string so PATH resolution
    happens at fire time. Operators on editable installs (most of us)
    can override with ``--metasphere-bin /abs/path``.
    """
    import shutil as _sh
    found = _sh.which("metasphere")
    return found or "metasphere"


def _register_cron(paths: Paths, *,
                    only_project: Optional[str] = None,
                    cron_expr: str = _DEFAULT_CRON_EXPR,
                    metasphere_bin: Optional[str] = None,
                    dry_run: bool = False) -> list[str]:
    """Add one ``audit-docs:<name>`` job per registered project.

    Idempotent: if a job with the same ``id`` already exists, it's
    left alone (no second entry, no overwrite). Returns the list of
    ids ADDED (empty list on no-op).
    """
    from .. import schedule as _schedule

    bin_path = metasphere_bin or _metasphere_bin()
    registry = _project._load_registry(paths)
    if only_project:
        registry = [e for e in registry if e.get("name") == only_project]
        if not registry:
            raise ValueError(f"no registered project: {only_project}")

    added: list[str] = []
    if dry_run:
        for entry in registry:
            jid = _audit_job_id(entry.get("name", ""))
            added.append(jid)
        return added

    with _schedule.with_locked_jobs(paths) as jobs:
        existing_ids = {j.id for j in jobs}
        before_count = len(jobs)
        for entry in registry:
            name = entry.get("name", "")
            if not name:
                continue
            jid = _audit_job_id(name)
            if jid in existing_ids:
                continue
            cmd = f"{bin_path} audit-docs --project {name}"
            jobs.append(_schedule.Job(
                id=jid,
                source="audit-docs",
                source_id=jid,
                agent_id="audit-docs",
                name=jid,
                enabled=True,
                kind="cron",
                cron_expr=cron_expr,
                tz="UTC",
                payload_kind="command",
                payload_message=cmd,
                command=cmd,
                full_command=cmd,
                session_target="isolated",
                wake_mode="next-heartbeat",
            ))
            added.append(jid)
        _schedule.save_jobs(jobs, paths, _input_count=before_count)
    return added


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="metasphere audit-docs",
        description="Scan commits since the last CHANGELOG entry and "
        "produce a draft stanza + README-staleness flags.",
    )
    sub = parser.add_subparsers(dest="cmd")

    # ``register-cron`` is a subcommand; the default (no subcommand)
    # behavior remains the audit run so existing cron entries keep
    # working after this PR.
    p_reg = sub.add_parser(
        "register-cron",
        help="Register daily audit-docs cron jobs (one per project).",
    )
    p_reg.add_argument("--project", default=None,
                        help="Only register for one project (default: all).")
    p_reg.add_argument("--cron-expr", default=_DEFAULT_CRON_EXPR,
                        help=f"Cron expression (default: {_DEFAULT_CRON_EXPR!r}).")
    p_reg.add_argument("--metasphere-bin", default=None,
                        help="Absolute path to the metasphere binary "
                        "(default: PATH lookup at registration time).")
    p_reg.add_argument("--dry-run", action="store_true",
                        help="List the jobs that WOULD be added.")

    # Default audit flags (hoisted to both the top-level parser and a
    # ``run`` subcommand so legacy invocations keep working).
    parser.add_argument("--project", default=None,
                        help="Registered project name to audit.")
    parser.add_argument("--output", type=Path, default=None,
                        help=f"Report dir (default: {REPORTS_ROOT}).")
    parser.add_argument("--no-notify", action="store_true",
                        help="Skip the !info message to @orchestrator.")

    args = parser.parse_args(argv)
    paths = resolve()

    if args.cmd == "register-cron":
        try:
            added = _register_cron(
                paths,
                only_project=args.project,
                cron_expr=args.cron_expr,
                metasphere_bin=args.metasphere_bin,
                dry_run=args.dry_run,
            )
        except ValueError as e:
            print(f"audit-docs: {e}", file=sys.stderr)
            return 2
        verb = "would add" if args.dry_run else "added"
        if added:
            print(f"audit-docs: {verb} {len(added)} job(s):")
            for jid in added:
                print(f"  - {jid}")
        else:
            print("audit-docs: no new jobs (all projects already registered)")
        return 0

    # Default: run an audit.
    if not args.project:
        parser.error("either run an audit (--project NAME) or use a subcommand "
                     "(register-cron).")
    rc, path = _run_audit(
        args.project, paths=paths,
        output_dir=args.output,
        notify=not args.no_notify,
    )
    if path != Path():
        print(f"audit-docs: report → {path}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
