"""Tests for metasphere.consolidate.

Builds a temp git repo with real ``.tasks/active/`` files and a real
``git log`` history, then exercises HIGH / MEDIUM / LOW classification
and the apply step (archive vs annotate vs noop).
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from metasphere import consolidate as _con
from metasphere import schedule as _sched
from metasphere import tasks as _tasks


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _git(repo: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        capture_output=True,
        text=True,
    )


@pytest.fixture
def repo(tmp_path: Path, monkeypatch) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "Test")
    # Seed an initial commit so HEAD exists.
    (repo / "README.md").write_text("seed\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-q", "-m", "seed")
    monkeypatch.chdir(repo)
    return repo


def _create_task(repo: Path, title: str) -> _tasks.Task:
    return _tasks.create_task(title, "!normal", repo, repo, created_by="@test")


def _commit(repo: Path, filename: str, message: str) -> str:
    (repo / filename).write_text("x\n")
    _git(repo, "add", filename)
    _git(repo, "commit", "-q", "-m", message)
    return subprocess.check_output(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
    ).strip()


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


def test_scan_active_tasks_returns_only_active(repo, tmp_paths):
    _create_task(repo, "alpha task")
    _create_task(repo, "beta task")
    found = _con.scan_active_tasks(repo)
    assert {t.title for t in found} == {"alpha task", "beta task"}


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------


def test_high_confidence_when_slug_in_commit_message(repo, tmp_paths):
    t = _create_task(repo, "Implement payment retry queue")
    _commit(repo, "f.txt", f"feat: ship {t.id} end-to-end")
    commits = _con._git_log(repo, "30d")
    ev = _con.find_evidence_for_task(t, commits)
    assert ev
    assert ev[0].verdict == _con.VERDICT_HIGH


def test_medium_confidence_when_title_tokens_overlap(repo, tmp_paths):
    t = _create_task(repo, "Refactor billing notification dispatcher")
    # Subject has 3/4 significant tokens (refactor, billing, notification, dispatcher)
    # — but NOT the slug literally.
    _commit(repo, "f.txt", "refactor billing notification dispatcher cleanup")
    commits = _con._git_log(repo, "30d")
    ev = _con.find_evidence_for_task(t, commits)
    assert ev
    # Slug is also present in this case (sluggified title); check medium fallback
    # works by stripping the slug match path:
    # The literal slug "refactor-billing-notification-dispatcher" won't appear
    # word-bounded in the subject ("refactor billing notification dispatcher
    # cleanup") because the slug uses hyphens. So it should be MEDIUM, not HIGH.
    assert ev[0].verdict == _con.VERDICT_MEDIUM


def test_low_confidence_when_no_signal(repo, tmp_paths):
    t = _create_task(repo, "Investigate quarterly report skew")
    _commit(repo, "f.txt", "chore: bump deps")
    commits = _con._git_log(repo, "30d")
    ev = _con.find_evidence_for_task(t, commits)
    assert ev == []


def test_common_words_alone_do_not_match(repo, tmp_paths):
    # Title is mostly stopwords — should not produce a false positive.
    t = _create_task(repo, "Add a new task for the team")
    _commit(repo, "f.txt", "Add a new feature for the user")
    commits = _con._git_log(repo, "30d")
    ev = _con.find_evidence_for_task(t, commits)
    assert ev == []


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------


def test_apply_high_archives_task(repo, tmp_paths):
    t = _create_task(repo, "Wire up cron consolidation")
    _commit(repo, "f.txt", f"build: {t.id} landed")
    report = _con.run_pass(repo_root=repo, since="30d", threshold="medium", paths=tmp_paths)
    actions = {r["task_id"]: r["action"] for r in report.results}
    assert actions[t.id] == "archived"
    # Active dir no longer holds it.
    assert not (repo / ".tasks" / "active" / f"{t.id}.md").exists()
    # Archive dir does.
    archive_root = repo / ".tasks" / "archive"
    archived_files = list(archive_root.rglob(f"{t.id}.md"))
    assert len(archived_files) == 1
    body = archived_files[0].read_text()
    assert "presumed-complete" in body


def test_apply_medium_annotates_only(repo, tmp_paths):
    t = _create_task(repo, "Refactor billing notification dispatcher")
    _commit(repo, "f.txt", "refactor billing notification dispatcher cleanup")
    report = _con.run_pass(repo_root=repo, since="30d", threshold="medium", paths=tmp_paths)
    actions = {r["task_id"]: r["action"] for r in report.results}
    assert actions[t.id] == "annotated"
    # Still active.
    active = repo / ".tasks" / "active" / f"{t.id}.md"
    assert active.exists()
    body = active.read_text()
    assert "possibly-completed" in body


def test_dry_run_does_not_mutate(repo, tmp_paths):
    t = _create_task(repo, "Migrate legacy harness pointer")
    _commit(repo, "f.txt", f"chore: {t.id} done")
    report = _con.run_pass(
        repo_root=repo, since="30d", threshold="medium", dry_run=True, paths=tmp_paths
    )
    assert report.results[0]["action"] == "would-archive"
    assert (repo / ".tasks" / "active" / f"{t.id}.md").exists()


def test_threshold_high_skips_medium(repo, tmp_paths):
    t = _create_task(repo, "Refactor billing notification dispatcher")
    _commit(repo, "f.txt", "refactor billing notification dispatcher cleanup")
    report = _con.run_pass(repo_root=repo, since="30d", threshold="high", paths=tmp_paths)
    actions = {r["task_id"]: r["action"] for r in report.results}
    assert actions[t.id] == "noop"


def test_emits_consolidate_event(repo, tmp_paths):
    t = _create_task(repo, "Wire up cron consolidation")
    _commit(repo, "f.txt", f"build: {t.id} landed")
    _con.run_pass(repo_root=repo, since="30d", threshold="medium", paths=tmp_paths)
    events_log = tmp_paths.events_log
    assert events_log.exists()
    lines = [json.loads(l) for l in events_log.read_text().splitlines() if l.strip()]
    consolidate_events = [e for e in lines if e["type"] == "task.consolidate"]
    assert consolidate_events
    assert any(e["meta"]["task_id"] == t.id and e["meta"]["verdict"] == "high" for e in consolidate_events)


# ---------------------------------------------------------------------------
# Schedule registration
# ---------------------------------------------------------------------------


def test_register_job_idempotent(repo, tmp_paths):
    j1 = _con.register_job(tmp_paths)
    j2 = _con.register_job(tmp_paths)
    assert j1.id == j2.id == _con.JOB_ID
    jobs = _sched.load_jobs(tmp_paths)
    matches = [j for j in jobs if j.id == _con.JOB_ID]
    assert len(matches) == 1
    assert matches[0].kind == "cron"
    assert matches[0].payload_kind == "command"


def test_unregister_job(repo, tmp_paths):
    _con.register_job(tmp_paths)
    assert _con.unregister_job(tmp_paths) is True
    jobs = _sched.load_jobs(tmp_paths)
    assert all(j.id != _con.JOB_ID for j in jobs)


# ---------------------------------------------------------------------------
# dispatch_command
# ---------------------------------------------------------------------------


def test_dispatch_command_executes_argv(tmp_path):
    marker = tmp_path / "ran"
    ok = _sched.dispatch_command(f"touch {marker}")
    assert ok is True
    assert marker.exists()


def test_dispatch_command_returns_false_on_failure():
    assert _sched.dispatch_command("false") is False


def test_dispatch_command_rejects_empty():
    assert _sched.dispatch_command("") is False
