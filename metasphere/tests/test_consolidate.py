"""Tests for metasphere.consolidate (lifecycle verdicts)."""

from __future__ import annotations

import datetime as _dt
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


def _set_updated(task: _tasks.Task, iso: str, repo: Path) -> _tasks.Task:
    # update_task bumps updated_at to now; we need to force it older.
    # Rewrite the file directly.
    text = task.path.read_text()
    new = text.replace(f"updated_at: {task.updated}", f"updated_at: {iso}")
    task.path.write_text(new)
    return _tasks.Task.from_text(task.path.read_text(), path=task.path)


def _iso(minutes_ago: int) -> str:
    dt = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=minutes_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


def test_scan_active_tasks_returns_only_active(repo, tmp_paths):
    _create_task(repo, "alpha task")
    _create_task(repo, "beta task")
    found = _con.scan_active_tasks(repo)
    assert {t.title for t in found} == {"alpha task", "beta task"}


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def test_classify_active_when_recently_updated(repo, tmp_paths):
    t = _create_task(repo, "fresh")
    t = _tasks.start_task(t.id, "@worker", repo)
    assert _con.classify_task(t, stale_window_minutes=15) == _con.VERDICT_ACTIVE


def test_classify_stale_when_owner_and_old(repo, tmp_paths):
    t = _create_task(repo, "old")
    t = _tasks.start_task(t.id, "@worker", repo)
    t = _set_updated(t, _iso(60), repo)
    assert _con.classify_task(t, stale_window_minutes=15) == _con.VERDICT_STALE


def test_classify_unowned_when_no_owner_and_old(repo, tmp_paths):
    t = _create_task(repo, "orphan")
    t = _set_updated(t, _iso(60), repo)
    assert _con.classify_task(t, stale_window_minutes=15) == _con.VERDICT_UNOWNED


def test_classify_blocked_on_status(repo, tmp_paths):
    t = _create_task(repo, "waiting")
    _tasks.update_task(t.id, repo, status="blocked: upstream")
    t = _tasks.Task.from_text(t.path.read_text(), path=t.path)
    t = _set_updated(t, _iso(60), repo)
    assert _con.classify_task(t, stale_window_minutes=15) == _con.VERDICT_BLOCKED


def test_classify_done_on_status(repo, tmp_paths):
    t = _create_task(repo, "fake-done")
    _tasks.update_task(t.id, repo, status="complete: all set")
    t = _tasks.Task.from_text(t.path.read_text(), path=t.path)
    assert _con.classify_task(t, stale_window_minutes=15) == _con.VERDICT_DONE


def test_cooldown_suppresses_reping(repo, tmp_paths):
    # stale but recently pinged → treated as ACTIVE
    t = _create_task(repo, "cooldown")
    t = _tasks.start_task(t.id, "@worker", repo)
    t = _set_updated(t, _iso(60), repo)
    _tasks.update_task(t.id, repo, last_pinged_at=_iso(5))
    t = _tasks.Task.from_text(t.path.read_text(), path=t.path)
    # update_task bumps updated_at, force it back.
    t = _set_updated(t, _iso(60), repo)
    assert _con.classify_task(t, stale_window_minutes=15) == _con.VERDICT_ACTIVE


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------


def test_legacy_task_without_new_fields_loads_with_defaults(repo, tmp_paths):
    t = _create_task(repo, "legacy")
    # Simulate pre-migration file by stripping the new keys.
    raw = t.path.read_text()
    lines = [l for l in raw.splitlines() if not l.startswith("last_pinged_at") and not l.startswith("ping_count")]
    t.path.write_text("\n".join(lines) + "\n")
    reloaded = _tasks.Task.from_text(t.path.read_text(), path=t.path)
    assert reloaded.last_pinged_at == ""
    assert reloaded.ping_count == 0


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------


class _FakeSender:
    def __init__(self):
        self.calls = []

    def __call__(self, target, label, body, from_agent, *, paths=None):
        self.calls.append({"target": target, "label": label, "body": body, "from": from_agent})
        return None


def _make_persistent(paths, agent_id: str):
    d = paths.agent_dir(agent_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "MISSION.md").write_text("test persona\n")


def test_ping_persistent_agent_sends_query(repo, tmp_paths):
    _make_persistent(tmp_paths, "@worker")
    t = _create_task(repo, "ping me")
    t = _tasks.start_task(t.id, "@worker", repo)
    t = _set_updated(t, _iso(60), repo)

    sender = _FakeSender()
    result = _con.apply_verdict(
        t, _con.VERDICT_STALE, repo, tmp_paths, sender=sender
    )
    assert result["action"] == "pinged"
    assert result["target"] == "@worker"
    assert len(sender.calls) == 1
    assert sender.calls[0]["label"] == "!query"
    assert t.id in sender.calls[0]["body"]

    # ping_count bumped + last_pinged_at set
    reloaded = _tasks.Task.from_text(t.path.read_text(), path=t.path)
    assert reloaded.ping_count == 1
    assert reloaded.last_pinged_at != ""


def test_stale_nonpersistent_owner_escalates_to_orchestrator(repo, tmp_paths):
    # No MISSION.md → @someone is ephemeral → escalate
    t = _create_task(repo, "ephemeral owner")
    t = _tasks.start_task(t.id, "@ephem", repo)
    t = _set_updated(t, _iso(60), repo)

    sender = _FakeSender()
    result = _con.apply_verdict(
        t, _con.VERDICT_STALE, repo, tmp_paths, sender=sender
    )
    assert result["action"] == "escalated-orchestrator"
    assert sender.calls[0]["target"] == "@orchestrator"
    assert sender.calls[0]["label"] == "!info"


def test_unowned_escalates_to_orchestrator(repo, tmp_paths):
    t = _create_task(repo, "orphan task")
    t = _set_updated(t, _iso(60), repo)

    sender = _FakeSender()
    result = _con.apply_verdict(
        t, _con.VERDICT_UNOWNED, repo, tmp_paths, sender=sender
    )
    assert result["action"] == "escalated-orchestrator"
    assert sender.calls[0]["target"] == "@orchestrator"


def test_ping_count_threshold_escalates_to_user(repo, tmp_paths):
    _make_persistent(tmp_paths, "@worker")
    t = _create_task(repo, "loud")
    t = _tasks.start_task(t.id, "@worker", repo)
    _tasks.update_task(t.id, repo, ping_count=5)
    t = _tasks.Task.from_text(t.path.read_text(), path=t.path)
    t = _set_updated(t, _iso(60), repo)

    sender = _FakeSender()
    telegrams: list[str] = []

    def fake_tg(body: str) -> bool:
        telegrams.append(body)
        return True

    result = _con.apply_verdict(
        t, _con.VERDICT_STALE, repo, tmp_paths,
        sender=sender, telegram_sender=fake_tg,
    )
    assert result["action"] == "escalated-user"
    assert telegrams
    assert t.id in telegrams[0]


def test_done_task_is_archived(repo, tmp_paths):
    t = _create_task(repo, "silent done")
    _tasks.update_task(t.id, repo, status="complete: externally")
    t = _tasks.Task.from_text(t.path.read_text(), path=t.path)

    result = _con.apply_verdict(
        t, _con.VERDICT_DONE, repo, tmp_paths
    )
    assert result["action"] == "archived"
    assert not (repo / ".tasks" / "active" / f"{t.id}.md").exists()


def test_active_noop(repo, tmp_paths):
    t = _create_task(repo, "fresh")
    sender = _FakeSender()
    result = _con.apply_verdict(
        t, _con.VERDICT_ACTIVE, repo, tmp_paths, sender=sender
    )
    assert result["action"] == "noop"
    assert sender.calls == []


def test_blocked_noop(repo, tmp_paths):
    t = _create_task(repo, "waiting")
    sender = _FakeSender()
    result = _con.apply_verdict(
        t, _con.VERDICT_BLOCKED, repo, tmp_paths, sender=sender
    )
    assert result["action"] == "noop"
    assert sender.calls == []


# ---------------------------------------------------------------------------
# Dry-run
# ---------------------------------------------------------------------------


def test_dry_run_does_not_mutate(repo, tmp_paths):
    _make_persistent(tmp_paths, "@worker")
    t = _create_task(repo, "keep-me")
    t = _tasks.start_task(t.id, "@worker", repo)
    t = _set_updated(t, _iso(60), repo)

    result = _con.apply_verdict(
        t, _con.VERDICT_STALE, repo, tmp_paths, dry_run=True
    )
    assert result["action"] == "would-ping"
    reloaded = _tasks.Task.from_text(t.path.read_text(), path=t.path)
    assert reloaded.ping_count == 0
    assert reloaded.last_pinged_at == ""


# ---------------------------------------------------------------------------
# run_pass integration
# ---------------------------------------------------------------------------


def test_run_pass_cooldown_prevents_reping(repo, tmp_paths):
    _make_persistent(tmp_paths, "@worker")
    t = _create_task(repo, "coolme")
    t = _tasks.start_task(t.id, "@worker", repo)
    t = _set_updated(t, _iso(60), repo)

    sender = _FakeSender()
    r1 = _con.run_pass(repo_root=repo, paths=tmp_paths, sender=sender)
    assert r1.results[0]["action"] == "pinged"
    assert len(sender.calls) == 1

    # Force updated_at old again (ping bumped it).
    t = _tasks.Task.from_text(t.path.read_text(), path=t.path)
    t = _set_updated(t, _iso(60), repo)

    r2 = _con.run_pass(repo_root=repo, paths=tmp_paths, sender=sender)
    # Cooldown: still only one send in total.
    assert r2.results[0]["action"] == "noop"
    assert len(sender.calls) == 1


def test_run_pass_git_commit_bumps_updated(repo, tmp_paths):
    _make_persistent(tmp_paths, "@worker")
    t = _create_task(repo, "code task")
    t = _tasks.start_task(t.id, "@worker", repo)
    t = _set_updated(t, _iso(60), repo)
    _commit(repo, "f.txt", f"feat: {t.id} landed")

    sender = _FakeSender()
    r = _con.run_pass(repo_root=repo, paths=tmp_paths, sender=sender, since="7d")
    # Commit references the slug → updated_at bumped → ACTIVE
    assert r.results[0]["verdict"] == _con.VERDICT_ACTIVE
    assert r.results[0]["action"] == "noop"
    assert sender.calls == []


def test_run_pass_emits_event(repo, tmp_paths):
    t = _create_task(repo, "orphan")
    _set_updated(t, _iso(60), repo)

    _con.run_pass(repo_root=repo, paths=tmp_paths, sender=_FakeSender())
    log = tmp_paths.events_log
    assert log.exists()
    lines = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    cons = [e for e in lines if e["type"] == "task.consolidate"]
    assert cons
    assert any(e["meta"]["task_id"] == t.id for e in cons)


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
    assert matches[0].cron_expr == "*/5 * * * *"


def test_unregister_job(repo, tmp_paths):
    _con.register_job(tmp_paths)
    assert _con.unregister_job(tmp_paths) is True
    jobs = _sched.load_jobs(tmp_paths)
    assert all(j.id != _con.JOB_ID for j in jobs)
