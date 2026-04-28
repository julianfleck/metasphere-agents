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


def _canonical_tasks(tmp_paths) -> Path:
    """Canonical ``.tasks/`` dir for the ``testproj`` registered by the
    tmp_paths fixture. After the 2026-04-15 project-dirs migration this
    is where tasks actually land — ``repo/.tasks/`` no longer holds
    them, so in-repo assertions in pre-migration tests must be swapped
    to this helper.
    """
    return tmp_paths.root / "projects" / "testproj" / ".tasks"


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


def _set_created(task: _tasks.Task, iso: str, repo: Path) -> _tasks.Task:
    text = task.path.read_text()
    new = text.replace(f"created: {task.created}", f"created: {iso}")
    task.path.write_text(new)
    return _tasks.Task.from_text(task.path.read_text(), path=task.path)


def _iso(minutes_ago: int) -> str:
    dt = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=minutes_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _iso_days(days_ago: int) -> str:
    dt = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(days=days_ago)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# Scan
# ---------------------------------------------------------------------------


def test_scan_active_tasks_returns_only_active(repo, tmp_paths):
    _create_task(repo, "alpha task")
    _create_task(repo, "beta task")
    found = _con.scan_active_tasks()
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


def test_classify_unassigned_sentinel_is_unowned(repo, tmp_paths):
    # @unassigned is the CLI-written sentinel for "no owner" — it must
    # classify as UNOWNED, not STALE. Otherwise these tasks fire
    # STALE→escalate_to_user every cooldown cycle (seen in prod: 10
    # tasks each accumulating 64-243 pings over 3 days).
    t = _create_task(repo, "cli-unassigned")
    t = _tasks.start_task(t.id, "@unassigned", repo)
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


def test_ping_routes_to_project_lead_before_assignee(repo, tmp_paths):
    """Julian 2026-04-15T08:55Z: when a task's project has a registered
    lead, the stale-task !query routes to that lead, not the task's
    ``assigned_to``. Otherwise external collaborators' tasks spam the
    project owner.
    """
    import json as _json
    _make_persistent(tmp_paths, "@julian")
    _make_persistent(tmp_paths, "@worldwire-lead")

    # Register "worldwire" with a lead member.
    reg = _json.loads((tmp_paths.root / "projects.json").read_text())
    reg.append({
        "name": "worldwire", "path": str(repo) + "/ww",
        "registered": "1970-01-01T00:00:00Z",
    })
    (tmp_paths.root / "projects.json").write_text(_json.dumps(reg))
    pdir = tmp_paths.projects / "worldwire"
    pdir.mkdir(parents=True, exist_ok=True)
    (pdir / "project.json").write_text(_json.dumps({
        "schema": 2, "name": "worldwire", "path": str(repo) + "/ww",
        "members": [{"id": "@worldwire-lead", "role": "lead", "persistent": True}],
    }))

    t = _create_task(repo, "ww task")
    t = _tasks.start_task(t.id, "@julian", repo)
    # Manually set project field on the task.
    _tasks.update_task(t.id, repo, project="worldwire")
    t = _tasks.Task.from_text(t.path.read_text(), path=t.path)
    t = _set_updated(t, _iso(60), repo)

    sender = _FakeSender()
    result = _con.apply_verdict(
        t, _con.VERDICT_STALE, repo, tmp_paths, sender=sender
    )
    assert result["action"] == "pinged"
    # Routed to lead, NOT @julian (the assignee).
    assert result["target"] == "@worldwire-lead"
    assert sender.calls[0]["target"] == "@worldwire-lead"


def test_paused_task_is_terminal_not_stale(repo, tmp_paths):
    """Julian 2026-04-15T08:55Z: a task with ``status: paused`` +
    old ``updated_at`` + an assignee set MUST classify as PAUSED, not
    STALE. Before PR #11 item 5 it came out STALE and re-escalated
    every 15-min cycle.
    """
    _make_persistent(tmp_paths, "@worker")
    t = _create_task(repo, "paused task")
    t = _tasks.start_task(t.id, "@worker", repo)
    _tasks.update_task(t.id, repo, status="paused: waiting on upstream")
    t = _tasks.Task.from_text(t.path.read_text(), path=t.path)
    t = _set_updated(t, _iso(60), repo)

    verdict = _con.classify_task(t, stale_window_minutes=15)
    assert verdict == _con.VERDICT_PAUSED

    # apply_verdict for PAUSED is a noop — no ping sent, no archive.
    sender = _FakeSender()
    result = _con.apply_verdict(
        t, _con.VERDICT_PAUSED, repo, tmp_paths, sender=sender,
    )
    assert result["action"] == "noop"
    assert sender.calls == []


def test_ping_falls_back_to_assignee_when_no_lead(repo, tmp_paths):
    """No project OR no lead member → fall back to task.assignee."""
    _make_persistent(tmp_paths, "@worker")
    # No project registered with lead.
    t = _create_task(repo, "leadless")
    t = _tasks.start_task(t.id, "@worker", repo)
    _tasks.update_task(t.id, repo, project="nonexistent-project")
    t = _tasks.Task.from_text(t.path.read_text(), path=t.path)
    t = _set_updated(t, _iso(60), repo)

    sender = _FakeSender()
    result = _con.apply_verdict(
        t, _con.VERDICT_STALE, repo, tmp_paths, sender=sender
    )
    assert result["target"] == "@worker"

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


def test_unowned_threshold_stops_escalating(repo, tmp_paths):
    # After ping_escalate_threshold escalations, UNOWNED goes silent
    # instead of pinging @orchestrator forever. The task stays in
    # place; operator can assign/archive/blocked anytime.
    t = _create_task(repo, "chronically orphaned")
    t = _set_updated(t, _iso(60), repo)
    _tasks.update_task(t.id, repo, ping_count=5)
    t = _tasks.Task.from_text(t.path.read_text(), path=t.path)

    sender = _FakeSender()
    result = _con.apply_verdict(
        t, _con.VERDICT_UNOWNED, repo, tmp_paths, sender=sender
    )
    assert result["action"] == "noop-pinged-out"
    # Critically: no new escalation !info sent.
    assert len(sender.calls) == 0


# ---------------------------------------------------------------------------
# ABANDONED — terminal verdict for orphan tasks that aged out
# ---------------------------------------------------------------------------


def test_classify_abandoned_when_unowned_pinged_out_and_old(repo, tmp_paths):
    # UNOWNED + ping_count >= threshold + created >= 3 days ago → ABANDONED
    t = _create_task(repo, "ancient orphan")
    t = _set_updated(t, _iso(60), repo)
    t = _set_created(t, _iso_days(4), repo)
    _tasks.update_task(t.id, repo, ping_count=5)
    t = _tasks.Task.from_text(t.path.read_text(), path=t.path)
    # update_task bumped updated_at; force it back
    t = _set_updated(t, _iso(60), repo)
    assert _con.classify_task(t, stale_window_minutes=15) == _con.VERDICT_ABANDONED


def test_classify_unowned_not_abandoned_when_recently_created(repo, tmp_paths):
    # Pinged out but only 1 day old → still UNOWNED, not ABANDONED
    t = _create_task(repo, "recent orphan")
    t = _set_updated(t, _iso(60), repo)
    t = _set_created(t, _iso_days(1), repo)
    _tasks.update_task(t.id, repo, ping_count=5)
    t = _tasks.Task.from_text(t.path.read_text(), path=t.path)
    t = _set_updated(t, _iso(60), repo)
    assert _con.classify_task(t, stale_window_minutes=15) == _con.VERDICT_UNOWNED


def test_classify_unowned_not_abandoned_when_not_pinged_out(repo, tmp_paths):
    # Old enough but ping_count below threshold → still UNOWNED
    t = _create_task(repo, "quiet orphan")
    t = _set_updated(t, _iso(60), repo)
    t = _set_created(t, _iso_days(7), repo)
    assert _con.classify_task(t, stale_window_minutes=15) == _con.VERDICT_UNOWNED


def test_classify_abandoned_respects_custom_age(repo, tmp_paths):
    # With abandoned_age_days=1, a 2-day-old pinged-out orphan is ABANDONED
    t = _create_task(repo, "tunable orphan")
    t = _set_updated(t, _iso(60), repo)
    t = _set_created(t, _iso_days(2), repo)
    _tasks.update_task(t.id, repo, ping_count=5)
    t = _tasks.Task.from_text(t.path.read_text(), path=t.path)
    t = _set_updated(t, _iso(60), repo)
    assert (
        _con.classify_task(t, stale_window_minutes=15, abandoned_age_days=1)
        == _con.VERDICT_ABANDONED
    )


# ---------------------------------------------------------------------------
# Orphan-assignee — task assigned to a GC'd ephemeral whose dir is gone
# ---------------------------------------------------------------------------


def test_classify_orphan_when_assignee_dir_missing_is_unowned(repo, tmp_paths):
    # Assignee names a now-defunct ephemeral (dir GC'd). With paths
    # passed, classify_task must route through UNOWNED, not STALE —
    # otherwise the task fires STALE escalations forever pinging
    # nobody. Reproduces the 25 worldwire-orphan tasks at ping 280-294
    # observed 2026-04-25T10:00Z.
    t = _create_task(repo, "orphan ephemeral")
    t = _tasks.start_task(t.id, "@ww-access-check", repo)
    t = _set_updated(t, _iso(60), repo)
    # No agent dir at any layout — global or project-scoped.
    assert _con.classify_task(
        t, stale_window_minutes=15, paths=tmp_paths
    ) == _con.VERDICT_UNOWNED


def test_classify_orphan_assignee_old_pinged_out_abandons(repo, tmp_paths):
    # Same orphan path must ride the ABANDONED progression: pinged out
    # AND created > 3 days ago → ABANDONED, archives out of active/.
    # This is the path that drains the 25 ww-orphan tasks naturally on
    # the next consolidate cycle instead of leaving them in active/.
    t = _create_task(repo, "ancient ephemeral orphan")
    t = _tasks.start_task(t.id, "@ww-cortex-bounce-push", repo)
    t = _set_updated(t, _iso(60), repo)
    t = _set_created(t, _iso_days(4), repo)
    _tasks.update_task(t.id, repo, ping_count=5)
    t = _tasks.Task.from_text(t.path.read_text(), path=t.path)
    t = _set_updated(t, _iso(60), repo)
    assert _con.classify_task(
        t, stale_window_minutes=15, paths=tmp_paths
    ) == _con.VERDICT_ABANDONED


def test_classify_stale_when_assignee_dir_present_global(repo, tmp_paths):
    # Live assignee (dir exists) at global scope must still classify
    # STALE — orphan check must NOT swallow live ephemerals or
    # persistent agents that are simply slow to respond.
    t = _create_task(repo, "alive owner")
    t = _tasks.start_task(t.id, "@worldwire-eng", repo)
    t = _set_updated(t, _iso(60), repo)
    (tmp_paths.agent_dir("@worldwire-eng")).mkdir(parents=True, exist_ok=True)
    (tmp_paths.agent_dir("@worldwire-eng") / "MISSION.md").write_text("x")
    assert _con.classify_task(
        t, stale_window_minutes=15, paths=tmp_paths
    ) == _con.VERDICT_STALE


def test_classify_stale_when_assignee_dir_present_project(repo, tmp_paths):
    # Same, but the agent dir lives under a project (e.g. @explorer
    # under projects/metasphere-agents/agents/) — must also classify
    # STALE, not UNOWNED.
    t = _create_task(repo, "project-scoped owner")
    t = _tasks.start_task(t.id, "@explorer", repo)
    t = _set_updated(t, _iso(60), repo)
    proj_agents = tmp_paths.projects / "metasphere-agents" / "agents" / "@explorer"
    proj_agents.mkdir(parents=True, exist_ok=True)
    (proj_agents / "status").write_text("working")
    assert _con.classify_task(
        t, stale_window_minutes=15, paths=tmp_paths
    ) == _con.VERDICT_STALE


def test_classify_stale_without_paths_keeps_legacy_semantics(repo, tmp_paths):
    # Backwards compat: when paths is not supplied, the orphan check is
    # disabled. Named assignees still classify STALE even if no agent
    # exists. Existing call sites that don't thread paths through must
    # not silently change behaviour.
    t = _create_task(repo, "no-paths orphan")
    t = _tasks.start_task(t.id, "@vanished-agent", repo)
    t = _set_updated(t, _iso(60), repo)
    assert _con.classify_task(t, stale_window_minutes=15) == _con.VERDICT_STALE


def test_apply_abandoned_archives_to_abandoned_bucket(repo, tmp_paths):
    t = _create_task(repo, "to be abandoned")
    src = t.path
    result = _con.apply_verdict(
        t, _con.VERDICT_ABANDONED, repo, tmp_paths
    )
    assert result["action"] == "archived-abandoned"
    assert result["verdict"] == _con.VERDICT_ABANDONED
    # File moved out of active/
    assert not src.exists()
    # File landed in archive/_abandoned/
    dest = _canonical_tasks(tmp_paths) / "archive" / "_abandoned" / f"{t.id}.md"
    assert dest.exists()
    # Status flipped to abandoned
    archived = _tasks.Task.from_text(dest.read_text(), path=dest)
    assert archived.status == _tasks.STATUS_ABANDONED


def test_apply_abandoned_dry_run_does_not_move(repo, tmp_paths):
    t = _create_task(repo, "dryrun abandon")
    src = t.path
    result = _con.apply_verdict(
        t, _con.VERDICT_ABANDONED, repo, tmp_paths, dry_run=True
    )
    assert result["action"] == "would-archive-abandoned"
    assert src.exists()
    assert not (_canonical_tasks(tmp_paths) / "archive" / "_abandoned" / f"{t.id}.md").exists()


def test_apply_abandoned_emits_consolidate_event(repo, tmp_paths):
    t = _create_task(repo, "loud abandon")
    _con.apply_verdict(t, _con.VERDICT_ABANDONED, repo, tmp_paths)

    log = tmp_paths.events_log
    assert log.exists()
    lines = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    cons = [
        e for e in lines
        if e["type"] == "task.consolidate"
        and e["meta"]["task_id"] == t.id
    ]
    assert cons
    assert cons[-1]["meta"]["verdict"] == _con.VERDICT_ABANDONED
    assert cons[-1]["meta"]["action"] == "archived-abandoned"


def test_run_pass_archives_abandoned_orphan(repo, tmp_paths):
    # End-to-end: a pinged-out orphan that's older than the abandon
    # window gets archived to _abandoned/ in a single consolidate pass.
    t = _create_task(repo, "end to end abandon")
    t = _set_updated(t, _iso(60), repo)
    t = _set_created(t, _iso_days(5), repo)
    _tasks.update_task(t.id, repo, ping_count=5)
    t = _tasks.Task.from_text(t.path.read_text(), path=t.path)
    t = _set_updated(t, _iso(60), repo)
    src = t.path

    sender = _FakeSender()
    r = _con.run_pass(project_root=repo, paths=tmp_paths, sender=sender)
    assert any(res["verdict"] == _con.VERDICT_ABANDONED for res in r.results)
    assert any(res["action"] == "archived-abandoned" for res in r.results)
    assert not src.exists()
    assert (_canonical_tasks(tmp_paths) / "archive" / "_abandoned" / f"{t.id}.md").exists()
    # No noisy escalation message sent.
    assert sender.calls == []


def test_ping_count_at_threshold_escalates_to_user_once(repo, tmp_paths):
    # ping_count EXACTLY at threshold → escalate user, single-shot.
    _make_persistent(tmp_paths, "@worker")
    t = _create_task(repo, "loud")
    t = _tasks.start_task(t.id, "@worker", repo)
    _tasks.update_task(t.id, repo, ping_count=_con.PING_ESCALATE_THRESHOLD_DEFAULT)
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
    # ping_count is bumped past threshold so the next fire goes silent
    reloaded = _tasks.Task.from_text(t.path.read_text(), path=t.path)
    assert reloaded.ping_count > _con.PING_ESCALATE_THRESHOLD_DEFAULT


def test_ping_count_past_threshold_goes_silent(repo, tmp_paths):
    # ping_count ABOVE threshold → noop-pinged-out, no telegram.
    # Mirrors MSG_VERDICT_STALE noop-pinged-out (c8a5110). Without
    # this arm the task escalates to @user every cooldown cycle
    # forever (witnessed 2026-04-25: 26 tasks, 104 escalations/h).
    _make_persistent(tmp_paths, "@worker")
    t = _create_task(repo, "chronic")
    t = _tasks.start_task(t.id, "@worker", repo)
    _tasks.update_task(t.id, repo, ping_count=_con.PING_ESCALATE_THRESHOLD_DEFAULT + 1)
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
    assert result["action"] == "noop-pinged-out"
    # Critically: no telegram, no orchestrator !info sent.
    assert telegrams == []
    assert sender.calls == []
    # ping_count still bumps so the branch stays hit on next fire.
    reloaded = _tasks.Task.from_text(t.path.read_text(), path=t.path)
    assert reloaded.ping_count > _con.PING_ESCALATE_THRESHOLD_DEFAULT + 1


def test_done_task_is_archived(repo, tmp_paths):
    t = _create_task(repo, "silent done")
    _tasks.update_task(t.id, repo, status="complete: externally")
    t = _tasks.Task.from_text(t.path.read_text(), path=t.path)

    result = _con.apply_verdict(
        t, _con.VERDICT_DONE, repo, tmp_paths
    )
    assert result["action"] == "archived"
    assert not (_canonical_tasks(tmp_paths) / "active" / f"{t.id}.md").exists()


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
    r1 = _con.run_pass(project_root=repo, paths=tmp_paths, sender=sender)
    assert r1.results[0]["action"] == "pinged"
    assert len(sender.calls) == 1

    # Force updated_at old again (ping bumped it).
    t = _tasks.Task.from_text(t.path.read_text(), path=t.path)
    t = _set_updated(t, _iso(60), repo)

    r2 = _con.run_pass(project_root=repo, paths=tmp_paths, sender=sender)
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
    r = _con.run_pass(project_root=repo, paths=tmp_paths, sender=sender, since="7d")
    # Commit references the slug → updated_at bumped → ACTIVE
    assert r.results[0]["verdict"] == _con.VERDICT_ACTIVE
    assert r.results[0]["action"] == "noop"
    assert sender.calls == []


def test_run_pass_emits_event(repo, tmp_paths):
    t = _create_task(repo, "orphan")
    _set_updated(t, _iso(60), repo)

    _con.run_pass(project_root=repo, paths=tmp_paths, sender=_FakeSender())
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


# ---------------------------------------------------------------------------
# Message lifecycle
# ---------------------------------------------------------------------------


from metasphere import messages as _msgs


def _send_msg(tmp_paths, label: str, body: str = "x") -> _msgs.Message:
    return _msgs.send_message(
        "@.", label, body, "@sender", paths=tmp_paths, wake=False
    )


def _age_msg(msg: _msgs.Message, *, created_min_ago: int = 0, read_min_ago: int | None = None) -> _msgs.Message:
    # Force-rewrite frontmatter fields for age manipulation.
    text = msg.path.read_text()
    if created_min_ago:
        text = text.replace(f"created: {msg.created}", f"created: {_iso(created_min_ago)}")
    if read_min_ago is not None:
        # Flip status to read and stamp read_at
        text = text.replace("status: unread", "status: read")
        text = text.replace("read_at:", f"read_at: {_iso(read_min_ago)}")
    msg.path.write_text(text)
    return _msgs.read_message(msg.path)


def test_msg_classify_pinned_task(repo, tmp_paths):
    m_ = _send_msg(tmp_paths, "!task")
    assert _con.classify_message(m_) == _con.MSG_VERDICT_PINNED


def test_msg_classify_pinned_query(repo, tmp_paths):
    m_ = _send_msg(tmp_paths, "!query")
    assert _con.classify_message(m_) == _con.MSG_VERDICT_PINNED


def test_msg_classify_unread_fresh_is_active(repo, tmp_paths):
    m_ = _send_msg(tmp_paths, "!info")
    assert _con.classify_message(m_) == _con.MSG_VERDICT_ACTIVE


def test_msg_classify_unread_old(repo, tmp_paths):
    m_ = _send_msg(tmp_paths, "!info")
    m_ = _age_msg(m_, created_min_ago=60)
    assert _con.classify_message(m_) == _con.MSG_VERDICT_UNREAD_OLD


def test_msg_classify_done_pending_archive(repo, tmp_paths):
    m_ = _send_msg(tmp_paths, "!info")
    _msgs.update_status(m_.path, "status", _msgs.STATUS_COMPLETED)
    m_ = _msgs.read_message(m_.path)
    assert _con.classify_message(m_) == _con.MSG_VERDICT_DONE_PENDING_ARCHIVE


def test_msg_classify_completed_pinned_label_still_archives(repo, tmp_paths):
    # Regression: when a !task or !query is explicitly closed via
    # `messages done`, it should archive on the next consolidate cycle —
    # NOT stay parked as MSG_VERDICT_PINNED forever. Completing IS the
    # explicit human action PINNED is supposed to protect; once acted
    # on, the message has done its job. The previous code checked the
    # PINNED label before the COMPLETED status and left closed pinned
    # messages stuck in inbox indefinitely (witnessed 2026-04-11).
    m_ = _send_msg(tmp_paths, "!task")
    _msgs.update_status(m_.path, "status", _msgs.STATUS_COMPLETED)
    m_ = _msgs.read_message(m_.path)
    assert _con.classify_message(m_) == _con.MSG_VERDICT_DONE_PENDING_ARCHIVE


def test_msg_classify_info_auto_archive(repo, tmp_paths):
    m_ = _send_msg(tmp_paths, "!info")
    m_ = _age_msg(m_, read_min_ago=120)
    assert _con.classify_message(m_) == _con.MSG_VERDICT_INFO_AUTO_ARCHIVE


def test_msg_classify_info_read_recent_is_active(repo, tmp_paths):
    m_ = _send_msg(tmp_paths, "!info")
    m_ = _age_msg(m_, read_min_ago=5)
    assert _con.classify_message(m_) == _con.MSG_VERDICT_ACTIVE


def test_msg_classify_reply_auto_archive(repo, tmp_paths):
    """!reply messages share the !info auto-archive window: read-without-
    reply is a valid terminal state for a conversational response. Before
    this fix, !reply fell through to MSG-STALE and escalated to
    @orchestrator on every cooldown forever (witnessed 2026-04-25).
    """
    m_ = _send_msg(tmp_paths, "!reply")
    m_ = _age_msg(m_, read_min_ago=120)
    assert _con.classify_message(m_) == _con.MSG_VERDICT_INFO_AUTO_ARCHIVE


def test_msg_classify_reply_read_recent_is_active(repo, tmp_paths):
    m_ = _send_msg(tmp_paths, "!reply")
    m_ = _age_msg(m_, read_min_ago=5)
    assert _con.classify_message(m_) == _con.MSG_VERDICT_ACTIVE


def test_msg_classify_done_auto_archive(repo, tmp_paths):
    """``!done`` notifications: terminal once aged past the auto-archive
    window regardless of read status. Previously required STATUS_READ +
    read_at — an unread !done would loop UNREAD-OLD→STALE forever.
    Post-P2 → MSG_VERDICT_DONE (distinct terminal verdict).
    """
    m_ = _send_msg(tmp_paths, "!done")
    m_ = _age_msg(m_, read_min_ago=120)
    assert _con.classify_message(m_) == _con.MSG_VERDICT_DONE


def test_msg_classify_done_unread_still_terminal_after_window(repo, tmp_paths):
    """Regression for the 2026-04-15 self-audit gap: an unread !done
    notification — e.g. spawned by ``msg done`` when the original
    sender never opens their inbox — must still terminate. Before P2,
    it hit UNREAD-OLD → stale-pinged forever.
    """
    import datetime as _dt
    m_ = _send_msg(tmp_paths, "!done")
    # Crucially: NO read_at. Created 2 hours ago, never viewed.
    old = (_dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=120))
    m_.created = old.strftime("%Y-%m-%dT%H:%M:%SZ")
    # Write back so re-read gets the aged timestamp.
    from metasphere.io import write_frontmatter_file
    write_frontmatter_file(m_.path, m_.to_frontmatter())
    m_ = _msgs.read_message(m_.path)
    assert m_.status == _msgs.STATUS_UNREAD
    assert _con.classify_message(m_) == _con.MSG_VERDICT_DONE


def test_msg_apply_done_verdict_archives(repo, tmp_paths):
    """MSG-DONE handler archives like INFO-AUTO-ARCHIVE but with a
    distinct 'done-auto-archive' reason in the event log.
    """
    m_ = _send_msg(tmp_paths, "!done")
    m_ = _age_msg(m_, read_min_ago=120)
    result = _con.apply_message_verdict(
        m_, _con.MSG_VERDICT_DONE, tmp_paths,
    )
    assert result["action"].startswith("archived")
    # Source file no longer in inbox/
    assert not m_.path.exists()


def test_msg_done_does_not_compound_over_simulated_24h(repo, tmp_paths):
    """24h @ 5min cadence = 288 consolidate ticks. A single !done
    notification must classify terminal ONCE and archive ONCE — not
    spawn new escalation messages on every tick.

    Before P2: every tick classified the unread !done as UNREAD-OLD
    → generated a new escalation !query → that query itself became
    stale → compounding. Now: first tick → MSG-DONE → archived,
    zero follow-ups.
    """
    import datetime as _dt
    m_ = _send_msg(tmp_paths, "!done")
    old = _dt.datetime.now(_dt.timezone.utc) - _dt.timedelta(minutes=120)
    m_.created = old.strftime("%Y-%m-%dT%H:%M:%SZ")
    from metasphere.io import write_frontmatter_file
    write_frontmatter_file(m_.path, m_.to_frontmatter())

    sender = _FakeSender()
    archived_count = 0
    for _ in range(288):  # simulated 24h @ 5min cadence
        if not m_.path.exists():
            break
        m_ = _msgs.read_message(m_.path)
        verdict = _con.classify_message(m_)
        result = _con.apply_message_verdict(
            m_, verdict, tmp_paths, sender=sender,
        )
        if result["action"].startswith("archived"):
            archived_count += 1
    assert archived_count == 1, "!done must terminate in exactly one tick"
    assert sender.calls == [], (
        f"!done must not spawn follow-up pings; got {len(sender.calls)} calls"
    )


def test_msg_classify_stale_nonpinned(repo, tmp_paths):
    # A !urgent that was read long ago and never followed up on. Uses
    # !urgent rather than !reply because !reply now auto-archives on the
    # !info path (read-without-reply is conversational, not stale).
    m_ = _send_msg(tmp_paths, "!urgent")
    m_ = _age_msg(m_, read_min_ago=60)
    assert _con.classify_message(m_) == _con.MSG_VERDICT_STALE


def test_msg_info_in_stale_window_does_not_ping(repo, tmp_paths):
    # !info read between the 15min stale window and the 60min INFO-AUTO-
    # ARCHIVE window must classify ACTIVE, not STALE. Witnessed
    # 2026-04-26 on msg-1777212455 + msg-1777219709: each got 3 STALE
    # pings at +15/+30/+45min (each ping = a !query message back to
    # @orchestrator) before INFO-AUTO-ARCHIVE landed at +60min. !info is
    # notification-shaped — read-without-action is fine, no ping
    # ladder required, the auto-archive catches it.
    for label in ("!info", "!reply"):
        m_ = _send_msg(tmp_paths, label)
        m_ = _age_msg(m_, read_min_ago=30)  # past stale, before auto-archive
        assert _con.classify_message(m_) == _con.MSG_VERDICT_ACTIVE, (
            f"{label} read 30min ago should be ACTIVE (auto-archive at 60m), "
            f"got {_con.classify_message(m_)}"
        )


def test_msg_info_past_auto_archive_window_archives(repo, tmp_paths):
    # Sanity: the auto-archive path still wins at +60min for !info.
    # Guards against the previous test silently masking a regression
    # where !info never archives.
    m_ = _send_msg(tmp_paths, "!info")
    m_ = _age_msg(m_, read_min_ago=70)
    assert _con.classify_message(m_) == _con.MSG_VERDICT_INFO_AUTO_ARCHIVE


_NO_READER_AGE_MIN = (
    _con.STALE_WINDOW_MINUTES_DEFAULT + _con.INFO_AUTO_ARCHIVE_AFTER_MINUTES
) // 2  # past stale window (15), before !done auto-archive window (60)


def test_msg_classify_stale_to_no_reader_system_agent_archives(repo, tmp_paths):
    # Loop 2 regression: a !done message addressed to @consolidate would
    # otherwise STALE-ping forever — the ping spawns another no-reader
    # message that itself ages into STALE on the next tick.
    m_ = _msgs.send_message(
        "@consolidate", "!done", "x", "@orchestrator", paths=tmp_paths, wake=False
    )
    m_ = _age_msg(m_, read_min_ago=_NO_READER_AGE_MIN)
    assert _con.classify_message(m_, paths=tmp_paths) == _con.MSG_VERDICT_INFO_AUTO_ARCHIVE


def test_msg_classify_stale_to_gcd_ephemeral_archives(repo, tmp_paths):
    # GC'd ephemeral agents are a third "no reader" class — their
    # agent_dir is rmtree'd on cleanup, so messages addressed to them
    # have nobody to follow up. Same auto-archive treatment.
    m_ = _msgs.send_message(
        "@dead-ephemeral", "!done", "x", "@orchestrator", paths=tmp_paths, wake=False
    )
    m_ = _age_msg(m_, read_min_ago=_NO_READER_AGE_MIN)
    assert not tmp_paths.agent_dir("@dead-ephemeral").exists()
    assert _con.classify_message(m_, paths=tmp_paths) == _con.MSG_VERDICT_INFO_AUTO_ARCHIVE


def test_msg_classify_unread_old_cooldown(repo, tmp_paths):
    # Freshly escalated UNREAD-OLD should go back to ACTIVE until the
    # cooldown window expires — without this, every 5-min consolidate
    # tick re-escalates the same old unread message forever.
    m_ = _send_msg(tmp_paths, "!info")
    m_ = _age_msg(m_, created_min_ago=60)
    _msgs.update_status(m_.path, "last_pinged_at", _iso(5))
    m_ = _msgs.read_message(m_.path)
    assert _con.classify_message(m_) == _con.MSG_VERDICT_ACTIVE


def test_msg_classify_from_consolidate_fresh_is_pinned(repo, tmp_paths):
    # Freshly sent @consolidate messages stay visible for one heartbeat
    # tick so the operator sees the escalation, then auto-archive.
    m_ = _msgs.send_message(
        "@.", "!info", "x", "@consolidate", paths=tmp_paths, wake=False
    )
    assert _con.classify_message(m_) == _con.MSG_VERDICT_PINNED


def test_msg_classify_from_consolidate_old_auto_archives(repo, tmp_paths):
    # After the 5-min window, @consolidate-authored messages auto-archive
    # to keep the inbox clean even though the cascade is broken.
    m_ = _msgs.send_message(
        "@.", "!info", "x", "@consolidate", paths=tmp_paths, wake=False
    )
    m_ = _age_msg(m_, created_min_ago=10)
    assert _con.classify_message(m_) == _con.MSG_VERDICT_INFO_AUTO_ARCHIVE


def test_msg_apply_unread_old_threshold_archives(repo, tmp_paths):
    # After ping_escalate_threshold escalations, UNREAD-OLD archives
    # instead of escalating forever.
    m_ = _send_msg(tmp_paths, "!info")
    m_ = _age_msg(m_, created_min_ago=60)
    _msgs.update_status(m_.path, "ping_count", "5")
    m_ = _msgs.read_message(m_.path)
    src = m_.path
    sender = _FakeSender()
    result = _con.apply_message_verdict(
        m_, _con.MSG_VERDICT_UNREAD_OLD, tmp_paths, sender=sender
    )
    assert result["action"] == "archived"
    assert not src.exists()
    # Critically: no new escalation !info sent.
    assert len(sender.calls) == 0


def test_msg_classify_stale_cooldown(repo, tmp_paths):
    # Uses !urgent (not !reply) since !reply now auto-archives via the
    # !info path before the cooldown check is reached.
    m_ = _send_msg(tmp_paths, "!urgent")
    m_ = _age_msg(m_, read_min_ago=60)
    _msgs.update_status(m_.path, "last_pinged_at", _iso(5))
    m_ = _msgs.read_message(m_.path)
    assert _con.classify_message(m_) == _con.MSG_VERDICT_ACTIVE


def test_msg_apply_done_pending_archive_moves_file(repo, tmp_paths):
    m_ = _send_msg(tmp_paths, "!info")
    _msgs.update_status(m_.path, "status", _msgs.STATUS_COMPLETED)
    m_ = _msgs.read_message(m_.path)
    src = m_.path
    result = _con.apply_message_verdict(
        m_, _con.MSG_VERDICT_DONE_PENDING_ARCHIVE, tmp_paths
    )
    assert result["action"] == "archived"
    assert not src.exists()


def test_msg_apply_info_auto_archive_moves_file(repo, tmp_paths):
    m_ = _send_msg(tmp_paths, "!info")
    m_ = _age_msg(m_, read_min_ago=120)
    src = m_.path
    result = _con.apply_message_verdict(
        m_, _con.MSG_VERDICT_INFO_AUTO_ARCHIVE, tmp_paths
    )
    assert result["action"] == "archived"
    assert not src.exists()


def test_msg_apply_pinned_noop(repo, tmp_paths):
    m_ = _send_msg(tmp_paths, "!task")
    src = m_.path
    result = _con.apply_message_verdict(
        m_, _con.MSG_VERDICT_PINNED, tmp_paths
    )
    assert result["action"] == "noop"
    assert src.exists()


def test_msg_apply_stale_pings_recipient(repo, tmp_paths):
    m_ = _send_msg(tmp_paths, "!reply")
    m_ = _age_msg(m_, read_min_ago=60)
    sender = _FakeSender()
    result = _con.apply_message_verdict(
        m_, _con.MSG_VERDICT_STALE, tmp_paths, sender=sender
    )
    assert result["action"] == "pinged"
    assert len(sender.calls) == 1
    assert sender.calls[0]["label"] == "!query"
    # ping_count bumped
    reloaded = _msgs.read_message(m_.path)
    assert reloaded.ping_count == 1
    assert reloaded.last_pinged_at != ""


def test_msg_apply_stale_at_threshold_escalates_once(repo, tmp_paths):
    # Boundary cycle: ping_count == threshold → single escalation to
    # @orchestrator. The cycles before were pings to the recipient.
    m_ = _send_msg(tmp_paths, "!urgent")
    m_ = _age_msg(m_, read_min_ago=60)
    _msgs.update_status(m_.path, "ping_count", "3")
    m_ = _msgs.read_message(m_.path)
    sender = _FakeSender()
    result = _con.apply_message_verdict(
        m_, _con.MSG_VERDICT_STALE, tmp_paths, sender=sender
    )
    assert result["action"] == "escalated-orchestrator"
    assert sender.calls[0]["target"] == "@orchestrator"


def test_msg_apply_stale_above_threshold_goes_silent(repo, tmp_paths):
    # Above-threshold cycles must not re-escalate. Witnessed bug
    # (2026-04-25): 19 !urgent messages stuck at ping_count 141-167
    # generated ~133 escalations in 3.4h flooding @orchestrator. The
    # first crossing already notified; subsequent cycles are
    # amplification, not signal. Mirrors task UNOWNED-pinged-out.
    m_ = _send_msg(tmp_paths, "!urgent")
    m_ = _age_msg(m_, read_min_ago=60)
    _msgs.update_status(m_.path, "ping_count", "10")
    m_ = _msgs.read_message(m_.path)
    sender = _FakeSender()
    result = _con.apply_message_verdict(
        m_, _con.MSG_VERDICT_STALE, tmp_paths, sender=sender
    )
    assert result["action"] == "noop-pinged-out"
    assert len(sender.calls) == 0
    # ping_count still bumped so the verdict tracks cycle counts.
    reloaded = _msgs.read_message(m_.path)
    assert reloaded.ping_count == 11


def test_msg_run_pass_archives_old_info(repo, tmp_paths):
    m1 = _send_msg(tmp_paths, "!info")
    m1 = _age_msg(m1, read_min_ago=120)
    m2 = _send_msg(tmp_paths, "!task")  # pinned, leave alone
    sender = _FakeSender()
    r = _con.run_pass(project_root=repo, paths=tmp_paths, sender=sender)
    assert any(res["action"] == "archived" for res in r.message_results)
    assert not m1.path.exists()
    assert m2.path.exists()


def test_pinned_message_noop_emits_no_event(repo, tmp_paths):
    # PINNED messages are seen every consolidate cycle but require no
    # action; emitting per-cycle events for them buries actionable
    # signal (~30k/day on prod 2026-04-25). Result still records noop
    # for renderers, but the events log stays quiet.
    m_ = _send_msg(tmp_paths, "!task")
    result = _con.apply_message_verdict(
        m_, _con.MSG_VERDICT_PINNED, tmp_paths
    )
    assert result["action"] == "noop"

    log = tmp_paths.events_log
    if log.exists():
        lines = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
        for e in lines:
            if e["type"] == "message.consolidate":
                assert e["meta"]["msg_id"] != m_.id


def test_active_task_noop_emits_no_event(repo, tmp_paths):
    # ACTIVE tasks are visited every cycle and need no action; suppress
    # their per-cycle events (12.5k/day measured on prod) so the log is
    # actionable. Counterpart to test_apply_abandoned_emits_consolidate_event
    # — non-noop verdicts still emit.
    _make_persistent(tmp_paths, "@worker")
    t = _create_task(repo, "active task")
    t = _tasks.start_task(t.id, "@worker", repo)
    # Fresh updated_at → ACTIVE → noop.
    _con.apply_verdict(t, _con.VERDICT_ACTIVE, repo, tmp_paths)

    log = tmp_paths.events_log
    if log.exists():
        lines = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
        for e in lines:
            if e["type"] == "task.consolidate":
                assert e["meta"]["task_id"] != t.id


def test_unregister_job(repo, tmp_paths):
    _con.register_job(tmp_paths)
    assert _con.unregister_job(tmp_paths) is True
    jobs = _sched.load_jobs(tmp_paths)
    assert all(j.id != _con.JOB_ID for j in jobs)


# ---------------------------------------------------------------------------
# Ephemeral agent GC — deliverable preservation
# ---------------------------------------------------------------------------


def _seed_ephemeral_agent(
    tmp_paths, name: str, *, files: dict[str, str], status: str = "complete: done"
) -> Path:
    """Create a fake completed ephemeral agent dir for GC testing.

    Ephemeral here means: no MISSION.md (so not persistent), status starts
    with ``complete`` (so the alive-session check is bypassed).
    """
    agent_dir = tmp_paths.agents / name
    agent_dir.mkdir(parents=True, exist_ok=True)
    (agent_dir / "status").write_text(status, encoding="utf-8")
    for fname, content in files.items():
        (agent_dir / fname).write_text(content, encoding="utf-8")
    return agent_dir


def test_gc_preserves_uppercase_REPORT_md_in_full(tmp_paths):
    """Regression: an audit agent that writes REPORT.md (uppercase) must
    not have its deliverable silently rmtree'd. The old whitelist only
    matched lowercase ``report.md`` — glob by .md extension now covers
    both. The full report must land in a sibling file, not just the
    2KB-truncated concatenated log.
    """
    big_report = "# Audit Report\n\n" + ("a citation line.\n" * 500)  # ~9KB
    assert len(big_report) > 2048  # prove we're over the log truncation budget

    _seed_ephemeral_agent(
        tmp_paths,
        "@audit-bot",
        files={
            "harness.md": "# Agent: @audit-bot\n\n## Task\n\naudit thing\n",
            "task": "audit thing\n",
            "REPORT.md": big_report,
        },
    )

    results = _con._gc_ephemeral_agents(tmp_paths, dry_run=False)

    # Agent dir itself is gone.
    assert not (tmp_paths.agents / "@audit-bot").exists()

    # Exactly one agent GC'd.
    assert len(results) == 1
    r = results[0]
    assert r["agent"] == "@audit-bot"
    assert r["reason"] == "completed"
    assert "REPORT.md" in r["preserved_files"]

    # Concatenated log exists and points at the preserved deliverable.
    log_file = tmp_paths.logs / "agents" / "_global" / "@audit-bot.log"
    assert log_file.is_file()
    log_text = log_file.read_text(encoding="utf-8")
    assert "deliverables" in log_text
    assert "REPORT.md" in log_text

    # Full deliverable preserved in a sibling file, not truncated.
    deliv_path = (
        tmp_paths.logs / "agents" / "_global" / "@audit-bot" / "REPORT.md"
    )
    assert deliv_path.is_file()
    assert deliv_path.read_text(encoding="utf-8") == big_report


def test_gc_preserves_multiple_md_deliverables(tmp_paths):
    """An agent can produce more than one .md deliverable (e.g. both
    FINDINGS.md and summary.md). Each gets its own preserved sibling.
    ``harness.md`` is still routed to the concatenated log, not the
    deliverables lane.
    """
    _seed_ephemeral_agent(
        tmp_paths,
        "@researcher",
        files={
            "harness.md": "# Agent: @researcher\n",
            "status": "complete: done",
            "FINDINGS.md": "# Findings\n\nthing 1\nthing 2\n",
            "summary.md": "# Summary\n\nall good\n",
        },
    )
    # status file written twice — _seed_ephemeral_agent wrote one
    # already, but the dict override lets us test the status-in-files
    # path too. Normalize:
    (tmp_paths.agents / "@researcher" / "status").write_text("complete: done")

    _con._gc_ephemeral_agents(tmp_paths, dry_run=False)

    base = tmp_paths.logs / "agents" / "_global" / "@researcher"
    assert (base / "FINDINGS.md").read_text().startswith("# Findings")
    assert (base / "summary.md").read_text().startswith("# Summary")
    # harness.md is bookkeeping, not a standalone deliverable file
    assert not (base / "harness.md").exists()


def test_gc_skips_persistent_agents(tmp_paths):
    """A persistent agent (has MISSION.md) must never be GC'd, even
    with a ``complete:`` status and a deliverable file present.
    """
    agent_dir = tmp_paths.agents / "@orchestrator"
    agent_dir.mkdir(parents=True)
    (agent_dir / "MISSION.md").write_text("# Mission\n")
    (agent_dir / "status").write_text("complete: done")
    (agent_dir / "REPORT.md").write_text("# Report\n")

    results = _con._gc_ephemeral_agents(tmp_paths, dry_run=False)

    assert results == []
    assert agent_dir.exists()
    assert (agent_dir / "REPORT.md").exists()


def test_gc_skips_persistent_agent_mid_bootstrap(tmp_paths):
    """Regression (2026-04-14): 9 newly-bootstrapped persistent personas
    were GC'd as dead because their scope dirs only had persona-index.md
    and SOUL.md — MISSION.md hadn't been written yet. persona-index.md
    alone must be enough to mark the dir persistent and exempt it from
    liveness-based GC, even with no tmux session, no pid, and no recent
    activity.
    """
    agent_dir = tmp_paths.agents / "@masked-eng"
    agent_dir.mkdir(parents=True)
    (agent_dir / "persona-index.md").write_text("# Persona Index — @masked-eng\n")
    (agent_dir / "SOUL.md").write_text("# Soul\n")
    # No MISSION.md, no status, no session, no pid — looks dead to the
    # old heuristic.

    results = _con._gc_ephemeral_agents(tmp_paths, dry_run=False)

    assert results == []
    assert agent_dir.exists()
    assert (agent_dir / "persona-index.md").exists()


def test_gc_uses_project_scoped_session_for_alive_check(tmp_paths):
    """Regression: when a (root-level) ephemeral agent dir shares its
    name with a project-scoped registry entry — e.g. a leftover stub
    at ``~/.metasphere/agents/@x/`` while the live agent is registered
    under ``project=research`` — the alive check must look at the
    project-aware session name. Bare ``session_name_for`` would target
    the wrong tmux session, mark the agent dead, and GC the stub
    erroneously. Sister-fix to the posthook deferred-cmd resolution
    bug; defends the GC classifier against the same project-scope
    drift the other sites suffer from.
    """
    from unittest import mock as _mock
    from metasphere.agents import AgentRecord

    agent_dir = tmp_paths.agents / "@brand-mentions"
    agent_dir.mkdir(parents=True)
    (agent_dir / "harness.md").write_text("# Agent: @brand-mentions\n")

    rec = AgentRecord(
        name="@brand-mentions",
        scope="",
        parent="",
        status="",
        spawned_at="",
        project="research",
    )

    checked: list[str] = []

    def fake_alive(name: str) -> bool:
        checked.append(name)
        return True  # alive under project-aware name

    with _mock.patch("metasphere.session.list_agents", return_value=[rec]), \
         _mock.patch("metasphere.agents.session_alive", fake_alive):
        results = _con._gc_ephemeral_agents(tmp_paths, dry_run=False)

    assert checked == ["metasphere-research-brand-mentions"], (
        f"expected project-aware session name, got {checked!r}"
    )
    # Agent is alive under project-aware name → must NOT be GC'd.
    assert results == []
    assert agent_dir.exists()


def test_gc_reaps_ephemeral_without_persona_index(tmp_paths):
    """Sanity check the other half: a true ephemeral (no MISSION.md, no
    persona-index.md) past its dead window still gets GC'd. The fix
    widens the skip predicate only for persistent markers — ephemeral
    behavior is unchanged.
    """
    agent_dir = tmp_paths.agents / "@one-shot"
    agent_dir.mkdir(parents=True)
    (agent_dir / "status").write_text("complete: done")
    (agent_dir / "harness.md").write_text("# Agent: @one-shot\n")

    results = _con._gc_ephemeral_agents(tmp_paths, dry_run=False)

    assert len(results) == 1
    assert results[0]["agent"] == "@one-shot"
    assert not agent_dir.exists()
