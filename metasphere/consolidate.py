"""Memory consolidation: scan active tasks, match against git history.

Walks every ``.tasks/active/*.md`` under the repo, asks ``git log`` for
commits in the recent window, and assigns each task one of three
verdicts:

* **high**   — commit subject/body references the task slug verbatim.
               Auto-archive (move to ``archive/YYYY-MM-DD/``) with a
               consolidation note.
* **medium** — commit subject contains >50% of the task title's
               significant tokens. Annotate the task with a "possibly
               completed via <sha>?" line; do not archive.
* **low**    — no signal. Leave alone.

Every verdict (including ``low``) emits a ``task.consolidate`` event so
the audit trail survives even when nothing was changed on disk.

Safety: this module never deletes anything. The only mutating action is
``tasks.complete_task`` (which moves to ``archive/``) and
``tasks.add_update`` (which appends a note). Both are reversible.

The bar for **high** is intentionally strict — the user explicitly
preferred leaving a stale task open over wrongly archiving a live one.
"""

from __future__ import annotations

import dataclasses
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import schedule as _sched
from . import tasks as _tasks
from .events import log_event
from .paths import Paths, resolve

# Verdict levels, ordered weakest → strongest.
VERDICT_LOW = "low"
VERDICT_MEDIUM = "medium"
VERDICT_HIGH = "high"
VERDICT_ORDER = {VERDICT_LOW: 0, VERDICT_MEDIUM: 1, VERDICT_HIGH: 2}

# Default lookback window for git log scanning.
DEFAULT_SINCE = "14d"

# Default minimum verdict that triggers a mutating action.
DEFAULT_THRESHOLD = VERDICT_MEDIUM

# Tokens that are too generic to count toward fuzzy title matching.
_STOPWORDS = frozenset(
    """
    a an the and or but for to of in on with from by at as is be it
    this that these those add fix update create make new use
    task tasks work do done feature bug
    """.split()
)

# Slug pattern: tasks slugs are lowercase hyphen-separated identifiers
# (see metasphere.tasks.slugify).
_SLUG_RE_CACHE: dict[str, re.Pattern[str]] = {}


# ---------------------------------------------------------------------------
# Schedule integration
# ---------------------------------------------------------------------------

JOB_ID = "metasphere-task-consolidate"
JOB_NAME = "task:consolidate"
JOB_CRON = "17 */4 * * *"  # every 4h at :17 (offset from heartbeat ticks)


def build_job() -> _sched.Job:
    """Construct the consolidate cron job (mirrors update.build_job)."""
    return _sched.Job(
        id=JOB_ID,
        source="consolidate",
        source_id=JOB_ID,
        agent_id="consolidate",
        name=JOB_NAME,
        enabled=True,
        kind="cron",
        cron_expr=JOB_CRON,
        tz="UTC",
        payload_kind="command",
        payload_message="metasphere consolidate run",
        command="metasphere consolidate run",
        full_command="metasphere consolidate run",
    )


def register_job(paths: Paths | None = None) -> _sched.Job:
    """Idempotently install/refresh the consolidate cron job."""
    paths = paths or resolve()
    paths.schedule.mkdir(parents=True, exist_ok=True)
    new_job = build_job()
    with _sched.with_locked_jobs(paths) as jobs:
        input_count = len(jobs)
        replaced = False
        for i, j in enumerate(jobs):
            if j.id == JOB_ID:
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
        if not kept and input_count > 0:
            paths.schedule_jobs.write_text("[]\n", encoding="utf-8")
            return True
        _sched.save_jobs(kept, paths, _input_count=input_count)
    return True


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


@dataclass
class Evidence:
    sha: str
    subject: str
    verdict: str
    score: float
    reason: str


@dataclass
class TaskVerdict:
    task: _tasks.Task
    verdict: str
    evidence: list[Evidence] = field(default_factory=list)

    @property
    def best(self) -> Evidence | None:
        if not self.evidence:
            return None
        return max(self.evidence, key=lambda e: (VERDICT_ORDER[e.verdict], e.score))


def scan_active_tasks(repo_root: Path) -> list[_tasks.Task]:
    """Return every task currently in any ``.tasks/active/`` under the repo."""
    repo_root = Path(repo_root).resolve()
    out: list[_tasks.Task] = []
    for tasks_dir in repo_root.rglob(".tasks"):
        active = tasks_dir / "active"
        if not active.is_dir():
            continue
        for f in sorted(active.glob("*.md")):
            try:
                out.append(_tasks.Task.from_text(f.read_text(encoding="utf-8"), path=f))
            except Exception:
                continue
    return out


# ---------------------------------------------------------------------------
# Evidence collection
# ---------------------------------------------------------------------------


def _significant_tokens(title: str) -> list[str]:
    raw = re.findall(r"[a-z0-9]+", title.lower())
    return [t for t in raw if len(t) >= 4 and t not in _STOPWORDS]


def _slug_pattern(slug: str) -> re.Pattern[str]:
    p = _SLUG_RE_CACHE.get(slug)
    if p is None:
        # Word boundary on either side. Slugs contain hyphens which are
        # not \w, so anchor with non-word lookarounds instead.
        p = re.compile(r"(?<![\w-])" + re.escape(slug) + r"(?![\w-])", re.IGNORECASE)
        _SLUG_RE_CACHE[slug] = p
    return p


_SINCE_SHORTHAND = re.compile(r"^(\d+)\s*([dwhm])$")


def _normalize_since(since: str) -> str:
    """Translate ``7d``/``2w``/``6h``/``30m`` into git's ``--since`` format."""
    m = _SINCE_SHORTHAND.match(since.strip())
    if not m:
        return since
    n, unit = m.group(1), m.group(2)
    word = {"d": "days", "w": "weeks", "h": "hours", "m": "minutes"}[unit]
    return f"{n} {word} ago"


def _git_log(repo_root: Path, since: str) -> list[tuple[str, str, str]]:
    """Return ``[(sha, subject, body)]`` for commits in the window.

    Uses a NUL-delimited format so commit messages with newlines stay
    intact.
    """
    sep = "\x1e"  # record separator
    fmt = f"%H%x09%s%x09%b{sep}"
    try:
        out = subprocess.check_output(
            ["git", "-C", str(repo_root), "log", f"--since={_normalize_since(since)}", f"--pretty=format:{fmt}"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    records: list[tuple[str, str, str]] = []
    for chunk in out.split(sep):
        chunk = chunk.strip("\n")
        if not chunk:
            continue
        parts = chunk.split("\t", 2)
        if len(parts) < 2:
            continue
        sha = parts[0]
        subject = parts[1]
        body = parts[2] if len(parts) > 2 else ""
        records.append((sha, subject, body))
    return records


def find_evidence_for_task(
    task: _tasks.Task,
    commits: list[tuple[str, str, str]],
) -> list[Evidence]:
    """Score each commit against this task. Returns evidence sorted strongest first."""
    slug = task.id
    slug_re = _slug_pattern(slug) if slug else None
    title_tokens = _significant_tokens(task.title or "")
    title_set = set(title_tokens)

    out: list[Evidence] = []
    for sha, subject, body in commits:
        verdict = VERDICT_LOW
        score = 0.0
        reason = ""

        haystack = f"{subject}\n{body}"

        # HIGH: slug literal hit anywhere in commit message.
        if slug_re and slug_re.search(haystack):
            verdict = VERDICT_HIGH
            score = 1.0
            reason = f"slug '{slug}' present in commit message"
            out.append(Evidence(sha=sha[:12], subject=subject, verdict=verdict, score=score, reason=reason))
            continue

        # MEDIUM: >50% of significant title tokens land in the subject.
        if title_set:
            subject_tokens = set(_significant_tokens(subject))
            overlap = title_set & subject_tokens
            ratio = len(overlap) / max(1, len(title_set))
            if ratio > 0.5 and len(overlap) >= 2:
                verdict = VERDICT_MEDIUM
                score = ratio
                reason = f"{len(overlap)}/{len(title_set)} title tokens in subject: {sorted(overlap)}"
                out.append(Evidence(sha=sha[:12], subject=subject, verdict=verdict, score=score, reason=reason))
                continue

        # LOW: no record (don't add — keeps output noise down).

    out.sort(key=lambda e: (VERDICT_ORDER[e.verdict], e.score), reverse=True)
    return out


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def _meets_threshold(verdict: str, threshold: str) -> bool:
    return VERDICT_ORDER[verdict] >= VERDICT_ORDER[threshold]


def apply_verdict(
    tv: TaskVerdict,
    repo_root: Path,
    *,
    threshold: str,
    dry_run: bool,
    paths: Paths | None = None,
) -> dict:
    """Apply the verdict to the task on disk and emit an event.

    Returns a small dict with the action taken (for the CLI to render).
    """
    paths = paths or resolve()
    best = tv.best
    action = "noop"
    sha = best.sha if best else ""
    note = ""

    # Decide what to do based on verdict + threshold.
    if tv.verdict == VERDICT_HIGH and _meets_threshold(VERDICT_HIGH, threshold):
        note = f"consolidation: presumed-complete via {sha}"
        if not dry_run:
            try:
                _tasks.complete_task(tv.task.id, note, repo_root)
                action = "archived"
            except Exception as e:  # pragma: no cover - defensive
                action = f"error:{e}"
        else:
            action = "would-archive"
    elif tv.verdict == VERDICT_MEDIUM and _meets_threshold(VERDICT_MEDIUM, threshold):
        note = f"possibly-completed via {sha}? ({best.reason if best else ''})"
        if not dry_run:
            try:
                _tasks.add_update(tv.task.id, note, repo_root)
                action = "annotated"
            except Exception as e:  # pragma: no cover - defensive
                action = f"error:{e}"
        else:
            action = "would-annotate"

    # Always emit an event — even for low/skip — for the audit trail.
    try:
        log_event(
            "task.consolidate",
            f"{tv.task.id}: {tv.verdict} → {action}",
            meta={
                "task_id": tv.task.id,
                "title": tv.task.title,
                "verdict": tv.verdict,
                "action": action,
                "threshold": threshold,
                "dry_run": dry_run,
                "sha": sha,
                "reason": best.reason if best else "",
                "evidence_count": len(tv.evidence),
            },
            paths=paths,
        )
    except Exception:  # pragma: no cover - defensive
        pass

    return {"task_id": tv.task.id, "verdict": tv.verdict, "action": action, "sha": sha, "note": note}


# ---------------------------------------------------------------------------
# Top-level pass
# ---------------------------------------------------------------------------


@dataclass
class ConsolidateReport:
    threshold: str
    since: str
    dry_run: bool
    results: list[dict] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.results:
            out[r["action"]] = out.get(r["action"], 0) + 1
        return out


def run_pass(
    *,
    repo_root: Path | None = None,
    since: str = DEFAULT_SINCE,
    threshold: str = DEFAULT_THRESHOLD,
    dry_run: bool = False,
    paths: Paths | None = None,
) -> ConsolidateReport:
    """One full consolidation pass over the repo."""
    paths = paths or resolve()
    repo_root = Path(repo_root) if repo_root else paths.repo
    if threshold not in VERDICT_ORDER:
        raise ValueError(f"invalid threshold {threshold!r}; want high|medium|low")

    tasks = scan_active_tasks(repo_root)
    commits = _git_log(repo_root, since)

    report = ConsolidateReport(threshold=threshold, since=since, dry_run=dry_run)
    for t in tasks:
        evidence = find_evidence_for_task(t, commits)
        verdict = evidence[0].verdict if evidence else VERDICT_LOW
        tv = TaskVerdict(task=t, verdict=verdict, evidence=evidence)
        result = apply_verdict(tv, repo_root, threshold=threshold, dry_run=dry_run, paths=paths)
        # Decorate with the title for nicer rendering.
        result["title"] = t.title
        report.results.append(result)

    return report
