"""``metasphere audit-docs`` — scan commits-since-last-CHANGELOG for doc drift.

Per-project audit: read the project's ``CHANGELOG.md``, find the date
of the newest entry, run ``git log --since=<date>`` in the registered
repo, classify the commits, and emit a draft CHANGELOG stanza plus a
README-staleness flag list.
"""

from __future__ import annotations

DESCRIPTION = "Scan commits since the last CHANGELOG entry for doc drift."

USAGE = """\
Usage: metasphere audit-docs --project <name> [options]
       metasphere audit-docs register-cron [options]

Default mode (audit): scan commits in the registered project repo
since the newest CHANGELOG entry, classify them, and write a markdown
report (CHANGELOG draft + README-staleness flags).

Options:
  --project <name>   Registered project name to audit (required).
  --output <dir>     Report directory (default: ~/.metasphere/audits/).
  --no-notify        Skip the !info message to @orchestrator.
  --since <window>   Override the CHANGELOG-derived window. Accepts a
                     bare YYYY-MM-DD (interpreted as 00:00:00 UTC, so
                     same-day commits are included) or any string git
                     --since understands.

Auto-generated `chore: bump version X.Y.Z → A.B.C` commits from the
bump-minor workflow are filtered out before classification — they
carry no CHANGELOG signal and would otherwise be stripped by hand.

Commits whose 7-char SHA prefix is already cited in the newest
CHANGELOG entry are also filtered out. This catches the same-day
boundary where commits and their documenting entry land on the
same UTC date and would otherwise be re-reported by the next
audit. The date floor stays as the coarse window; the SHA filter
is the fine cutter.

Subcommand `register-cron`:
  --project <name>          Only register one project (default: all).
  --cron-expr "<expr>"      Cron expression (default: "0 18 * * *").
  --metasphere-bin <path>   Absolute path to the metasphere binary.
  --dry-run                 List jobs that would be added.

Exit codes:
  0  report produced, no staleness flags
  1  report produced, staleness flags raised
  2  precondition failed (unknown project, no repo)
"""


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

#: Auto-generated version-bump commits produced by the bump-minor
#: GitHub Action carry zero CHANGELOG signal — they get filtered out
#: of the audit before classification so they don't recycle every
#: cycle as Chores noise. Tolerates both unicode arrow (the bot's
#: shape) and ascii ``->`` (a manual bump), and the trailing
#: ``[skip ci]`` marker is optional.
_AUTO_VERSION_BUMP_RE = re.compile(
    r"^chore:\s*bump version\s+\d+\.\d+\.\d+\s*(?:→|->)\s*\d+\.\d+\.\d+"
    r"(?:\s*\[skip ci\])?\s*$"
)


def _is_auto_version_bump(subject: str) -> bool:
    """Return True if ``subject`` is an auto-version-bump commit."""
    return bool(_AUTO_VERSION_BUMP_RE.match(subject.strip()))


def _is_changelog_self_update(subject: str) -> bool:
    """Return True if the commit's job is to write the CHANGELOG entry
    that documents prior work — i.e. a ``docs(changelog):`` commit.

    Structural false-positive class missed by the same-day SHA filter
    in ``_changelog_documented_shas``: the entry being added can't
    cite the meta-commit's own SHA, so on the next audit the
    bookkeeping commit gets re-reported as if it needed a CHANGELOG
    entry of its own. Same shape as the auto-version-bump filter (and
    the staleness-flag docs-skip) — drop them here so the operator
    doesn't see the entry they just shipped re-cited as new work.
    """
    return subject.strip().lower().startswith("docs(changelog):")


def _changelog_newest_date(changelog: Path) -> Optional[str]:
    """Extract the newest ISO date from a CHANGELOG.md. Looks for lines
    starting with ``## `` in one of three shapes:

    * bracketed ISO timestamp (``## [2026-04-15T...]``)
    * bare ISO date (``## 2026-04-15 — foo``)
    * date range (``## 2026-05-25 to 2026-05-27 — foo``) — returns
      the END of the range; the audit window starts AFTER the last
      documented day, so the next audit doesn't re-report commits
      already covered by the trailing range entry.

    Returns ``YYYY-MM-DD`` or ``None`` if no date found.
    """
    if not changelog.is_file():
        return None
    try:
        text = changelog.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    #: Accept ``## [2026-04-15T...]``, ``## 2026-04-15 — foo``, and
    #: ``## 2026-05-25 to 2026-05-27 — foo``. When the optional
    #: ``to <date>`` suffix is present, prefer that end-date.
    date_re = re.compile(
        r"^##\s+\[?(\d{4}-\d{2}-\d{2})(?:[^\n]*?\s+to\s+(\d{4}-\d{2}-\d{2}))?"
    )
    for line in text.splitlines():
        m = date_re.match(line)
        if m:
            return m.group(2) or m.group(1)
    return None


def _changelog_documented_shas(changelog: Path) -> set[str]:
    """Return the set of 7-char SHA prefixes already cited in the
    newest CHANGELOG entry's body.

    Same-day audits trip an awkward boundary: the date floor is UTC
    midnight (kept liberal so an entry written early in the day still
    captures later commits — see ``_normalize_since``), so commits
    landed and documented in the same day get re-reported by the next
    audit. Resolution is to read the SHAs the newest entry already
    cites — every entry uses the ``(`abc1234`)`` convention — and
    filter those out of the audit's commit list. Date-floor stays as
    the coarse window; SHA-filter is the fine cutter.

    Body = lines between the FIRST ``## `` heading and the next
    ``## `` heading or ``---`` boundary. Returns an empty set if no
    CHANGELOG or no SHA references found.
    """
    if not changelog.is_file():
        return set()
    try:
        text = changelog.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return set()
    in_entry = False
    body: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if in_entry:
                break  # second heading = end of newest entry
            in_entry = True
            continue
        if not in_entry:
            continue
        if line.strip() == "---":
            break
        body.append(line)
    sha_re = re.compile(r"`([0-9a-f]{7,40})`")
    return {m.lower()[:7] for m in sha_re.findall("\n".join(body))}


_DATE_ONLY_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _normalize_since(since: str) -> str:
    """Render a ``YYYY-MM-DD`` as an unambiguous ``YYYY-MM-DD 00:00:00
    +0000`` string so ``git log --since`` doesn't miss same-day
    commits due to local-time interpretation.

    Symptom 2026-04-15: ``--since 2026-04-15`` run on 2026-04-15
    could return "no new commits" because git interpreted the bare
    date in local-TZ, pushing the cutoff forward of UTC commits made
    earlier that same day. Explicit UTC midnight fixes it — commits
    with a UTC timestamp ``>= YYYY-MM-DD 00:00:00Z`` are included.

    Non-date strings (e.g. ``"2 days ago"`` or an explicit timestamp)
    pass through unchanged.
    """
    if _DATE_ONLY_RE.match(since):
        return f"{since} 00:00:00 +0000"
    return since


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
             f"--since={_normalize_since(since)}",
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

    `docs(...)`/`docs:` commits are skipped — they ARE the doc update,
    so flagging them as "needs doc update" is a self-referential false
    positive (see 2026-05-28 audit: `docs(changelog): ... canonical-name
    drift sweep` tripped the `canonical` keyword on the very commit that
    documented the canonical-name sweep).
    """
    flags: List[str] = []
    for rec in records:
        if _classify_subject(rec["subject"]) == "docs":
            continue
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
                notify: bool = True,
                since_override: Optional[str] = None) -> tuple[int, Path]:
    """Execute an audit for one project.

    Returns ``(exit_code, report_path)``. Exit codes:
      * 0 — report produced, no staleness flags
      * 1 — report produced, staleness flags raised
      * 2 — precondition failure (no such project, no repo)

    ``since_override`` bypasses ``_changelog_newest_date`` — useful when
    the operator wants to re-audit a known window.
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
    if since_override:
        since = since_override
        documented: set[str] = set()
    else:
        since = _changelog_newest_date(changelog)
        if since is None:
            # No CHANGELOG or no datable entries — audit the last 7 days.
            since = (_dt.date.today() - _dt.timedelta(days=7)).isoformat()
        documented = _changelog_documented_shas(changelog)

    records = _git_log_since(repo, since)
    if documented:
        records = [r for r in records if r["sha"][:7].lower() not in documented]
    records = [r for r in records if not _is_auto_version_bump(r["subject"])]
    records = [r for r in records if not _is_changelog_self_update(r["subject"])]
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
    args_list = list(sys.argv[1:] if argv is None else argv)
    if args_list and args_list[0] in ("--help", "-h"):
        sys.stdout.write(USAGE)
        return 0
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
    parser.add_argument(
        "--since", default=None,
        help="Override the CHANGELOG-derived window. Accepts a bare "
        "``YYYY-MM-DD`` (interpreted as 00:00:00 UTC — same-day commits "
        "included), or any string git's ``--since`` understands.",
    )

    args = parser.parse_args(args_list)
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
        since_override=args.since,
    )
    if path != Path():
        print(f"audit-docs: report → {path}")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
