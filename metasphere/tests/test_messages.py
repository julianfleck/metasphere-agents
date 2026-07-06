"""Tests for metasphere.messages.

Covers ordered frontmatter round-trips, field updates under flock, and
parent scope walking.
"""

from __future__ import annotations

import multiprocessing as mp
import os
from pathlib import Path

import pytest

from metasphere import messages as m
from metasphere.io import read_frontmatter_file


# ---------------------------------------------------------------------------
# Roundtrip
# ---------------------------------------------------------------------------


def test_send_and_read_roundtrip(tmp_paths):
    msg = m.send_message(
        "@..", "!info", "hello there", "@child", paths=tmp_paths, wake=False
    )
    assert msg.id.startswith("msg-")
    assert msg.path is not None and msg.path.exists()

    loaded = m.read_message(msg.path)
    assert loaded.id == msg.id
    assert loaded.from_ == "@child"
    assert loaded.to == "@.."
    assert loaded.label == "!info"
    assert loaded.status == m.STATUS_UNREAD
    assert "hello there" in loaded.body

    # Outbox copy exists at canonical location (~/.metasphere/projects/
    # testproj/.messages/outbox/). The tmp_paths fixture registers the
    # repo as 'testproj' so Project.for_cwd(scope) resolves to it.
    outbox = (tmp_paths.projects / "testproj" / ".messages" / "outbox"
              / f"{msg.id}.msg")
    assert outbox.exists(), f"outbox copy not at {outbox}"


# ---------------------------------------------------------------------------
# Inbox walking
# ---------------------------------------------------------------------------


def test_collect_inbox_returns_project_and_global(tmp_paths):
    """Canonical layout (PR #10): one ``.messages/inbox/`` per project
    plus the global bucket. ``collect_inbox`` returns
    project-for-scope + global; the old per-subdir nested walk doesn't
    apply because subdirectories no longer carry their own inboxes.
    """
    testproj_inbox = (tmp_paths.projects / "testproj" / ".messages" / "inbox")
    global_inbox = tmp_paths.root / "messages" / "inbox"
    testproj_inbox.mkdir(parents=True, exist_ok=True)
    global_inbox.mkdir(parents=True, exist_ok=True)

    for inbox, label in [(testproj_inbox, "!proj"), (global_inbox, "!global")]:
        msg = m.Message(
            id=f"msg-{label.strip('!')}",
            from_="@sender",
            to="@.",
            label=label,
            status=m.STATUS_UNREAD,
            scope="/",
            created="2026-04-07T00:00:00Z",
            body="\nbody\n",
        )
        m.write_message(msg, inbox / f"{msg.id}.msg")

    # Collecting from the project scope → sees project + global.
    msgs = m.collect_inbox(tmp_paths.project_root, tmp_paths.project_root)
    assert {x.label for x in msgs} == {"!proj", "!global"}


def test_send_to_absolute_path_target_routes_to_global(tmp_paths, tmp_path):
    """Canonical layout (PR #10): ``@/abs/path/`` still resolves the
    target scope to the absolute filesystem path (the doubled-prefix
    bug is still prevented) but the MESSAGE itself lands in the
    canonical per-project / global bucket, not at the scope dir.

    Since the abs path isn't a registered project, the message goes
    to ``~/.metasphere/messages/inbox/`` (the global sentinel).
    """
    abs_target = tmp_path / "elsewhere" / "scope"
    abs_target.mkdir(parents=True)

    msg = m.send_message(
        f"@/{abs_target}/",
        "!info",
        "absolute target",
        "@sender",
        paths=tmp_paths,
        wake=False,
    )
    # Canonical global inbox, not the abs_target itself.
    global_inbox = tmp_paths.root / "messages" / "inbox" / f"{msg.id}.msg"
    assert global_inbox.exists(), f"message not in global inbox: {global_inbox}"
    # Not in the abs_target scope.
    assert not (abs_target / ".messages" / "inbox" / f"{msg.id}.msg").exists()
    # Not in the old doubled-prefix location either.
    doubled = tmp_paths.project_root / str(abs_target).lstrip("/")
    assert not (doubled / ".messages" / "inbox" / f"{msg.id}.msg").exists()


# ---------------------------------------------------------------------------
# Frontmatter integrity
# ---------------------------------------------------------------------------


def test_update_status_preserves_ordering_and_body(tmp_paths):
    msg = m.send_message(
        "@..", "!task", "do the thing\nwith newlines", "@a", paths=tmp_paths, wake=False
    )
    p = msg.path

    # Snapshot field order before
    fm_before = read_frontmatter_file(p)
    keys_before = list(fm_before.meta.keys())
    body_before = fm_before.body

    m.update_status(p, "status", m.STATUS_READ)
    m.update_status(p, "read_at", "2026-04-07T12:00:00Z")

    fm_after = read_frontmatter_file(p)
    assert list(fm_after.meta.keys()) == keys_before, "field order changed"
    assert fm_after.meta["status"] == m.STATUS_READ
    assert fm_after.meta["read_at"] == "2026-04-07T12:00:00Z"
    # Other fields survived intact
    assert fm_after.meta["id"] == msg.id
    assert fm_after.meta["label"] == "!task"
    # Body unchanged (modulo leading newline normalization)
    assert fm_after.body.strip() == body_before.strip()
    assert "do the thing" in fm_after.body


# ---------------------------------------------------------------------------
# Locking under concurrent writers
# ---------------------------------------------------------------------------


def _hammer(path_str: str, field_name: str, value: str, n: int) -> None:
    # Each child repeatedly sets the same field. With flock, the file
    # must always be parseable and land in a coherent final state.
    from metasphere import messages as mm
    p = Path(path_str)
    for _ in range(n):
        mm.update_status(p, field_name, value)


def test_file_lock_prevents_interleaved_writes(tmp_paths):
    msg = m.send_message(
        "@..", "!task", "concurrent", "@a", paths=tmp_paths, wake=False
    )
    p = str(msg.path)

    procs = [
        mp.Process(target=_hammer, args=(p, "status", "read", 30)),
        mp.Process(target=_hammer, args=(p, "read_at", "2026-04-07T00:00:00Z", 30)),
    ]
    for pr in procs:
        pr.start()
    for pr in procs:
        pr.join(timeout=30)
        assert pr.exitcode == 0, "hammer process crashed (likely torn write)"

    # Final state must still be a valid, fully-populated message file
    loaded = m.read_message(Path(p))
    assert loaded.id == msg.id
    assert loaded.status == "read"
    assert loaded.read_at == "2026-04-07T00:00:00Z"
    assert loaded.from_ == "@a"  # untouched fields survived


# ---------------------------------------------------------------------------
# Reply flow
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# @-mention parsing
# ---------------------------------------------------------------------------


def _seed_project(tmp_paths, name: str) -> None:
    from metasphere.io import write_json
    pf = tmp_paths.root / "projects.json"
    data = []
    if pf.exists():
        import json
        data = json.loads(pf.read_text())
    data.append({"name": name, "path": "/tmp/" + name, "registered": "2026-04-08T00:00:00Z"})
    write_json(pf, data)


def _seed_agent(tmp_paths, name: str) -> None:
    (tmp_paths.root / "agents" / f"@{name}").mkdir(parents=True, exist_ok=True)


def test_extract_mentions_project_only(tmp_paths):
    _seed_project(tmp_paths, "example-project")
    ms = m.extract_mentions("hey @example-project take a look", paths=tmp_paths)
    assert len(ms) == 1
    assert ms[0].name == "example-project"
    assert ms[0].type == "project"
    assert ms[0].raw == "@example-project"


def test_extract_mentions_agent_only(tmp_paths):
    _seed_agent(tmp_paths, "synthetic-user")
    ms = m.extract_mentions("ping @synthetic-user please", paths=tmp_paths)
    assert [(x.name, x.type) for x in ms] == [("synthetic-user", "agent")]


def test_extract_mentions_collision_project_wins(tmp_paths):
    _seed_project(tmp_paths, "example-project")
    _seed_agent(tmp_paths, "example-project")
    ms = m.extract_mentions("@example-project hi", paths=tmp_paths)
    assert len(ms) == 1
    assert ms[0].type == "project"


def test_extract_mentions_unknown(tmp_paths):
    ms = m.extract_mentions("@nobody around?", paths=tmp_paths)
    assert len(ms) == 1
    assert ms[0].type == "unknown"
    assert ms[0].name == "nobody"


def test_view_marks_info_read_and_stamps_read_at(tmp_paths):
    msg = m.send_message(
        "@..", "!info", "fyi", "@child", paths=tmp_paths, wake=False
    )
    assert msg.status == m.STATUS_UNREAD
    loaded = m.read_message(msg.path, view=True)
    assert loaded.status == m.STATUS_READ
    assert loaded.read_at != ""
    # Persisted
    reloaded = m.read_message(msg.path)
    assert reloaded.status == m.STATUS_READ
    assert reloaded.read_at == loaded.read_at


def test_view_does_not_mark_task_messages_read(tmp_paths):
    msg = m.send_message(
        "@..", "!task", "do the thing", "@child", paths=tmp_paths, wake=False
    )
    loaded = m.read_message(msg.path, view=True)
    assert loaded.status == m.STATUS_UNREAD
    assert loaded.read_at == ""


def test_view_does_not_mark_query_messages_read(tmp_paths):
    msg = m.send_message(
        "@..", "!query", "ping?", "@child", paths=tmp_paths, wake=False
    )
    loaded = m.read_message(msg.path, view=True)
    assert loaded.status == m.STATUS_UNREAD


def test_view_no_op_without_flag(tmp_paths):
    msg = m.send_message(
        "@..", "!info", "fyi", "@child", paths=tmp_paths, wake=False
    )
    loaded = m.read_message(msg.path)
    assert loaded.status == m.STATUS_UNREAD
    assert loaded.read_at == ""


def test_collect_inbox_view_marks_nonsacred_read(tmp_paths):
    m.send_message("@.", "!info", "a", "@c", paths=tmp_paths, wake=False)
    m.send_message("@.", "!task", "b", "@c", paths=tmp_paths, wake=False)
    m.send_message("@.", "!done", "c", "@c", paths=tmp_paths, wake=False)
    msgs = m.collect_inbox(tmp_paths.scope, tmp_paths.project_root, view=True)
    by_label = {mm.label: mm for mm in msgs}
    assert by_label["!info"].status == m.STATUS_READ
    assert by_label["!done"].status == m.STATUS_READ
    assert by_label["!task"].status == m.STATUS_UNREAD


def test_mark_read_flips_info_status(tmp_paths):
    msg = m.send_message(
        "@.", "!info", "fyi", "@child", paths=tmp_paths, wake=False
    )
    assert msg.status == m.STATUS_UNREAD
    after = m.mark_read(msg.id, paths=tmp_paths)
    assert after.status == m.STATUS_READ
    assert after.read_at != ""
    reloaded = m.read_message(msg.path)
    assert reloaded.status == m.STATUS_READ
    assert reloaded.read_at == after.read_at


def test_mark_read_leaves_task_unread(tmp_paths):
    msg = m.send_message(
        "@.", "!task", "do the thing", "@child", paths=tmp_paths, wake=False
    )
    after = m.mark_read(msg.id, paths=tmp_paths)
    assert after.status == m.STATUS_UNREAD
    assert after.read_at == ""
    reloaded = m.read_message(msg.path)
    assert reloaded.status == m.STATUS_UNREAD
    assert reloaded.read_at == ""


def test_mark_read_leaves_query_unread(tmp_paths):
    msg = m.send_message(
        "@.", "!query", "ping?", "@child", paths=tmp_paths, wake=False
    )
    after = m.mark_read(msg.id, paths=tmp_paths)
    assert after.status == m.STATUS_UNREAD
    assert after.read_at == ""


def test_reply_marks_original_and_sets_reply_to(tmp_paths):
    # Original message lands in the scope inbox, as if a peer sent it.
    orig = m.send_message(
        "@.", "!query", "how do?", "@other", paths=tmp_paths, wake=False
    )
    assert orig.path is not None and orig.path.exists()

    reply = m.reply_to_message(orig.id, "like this", "@me", paths=tmp_paths)

    # Original marked replied
    reloaded = m.read_message(orig.path)
    assert reloaded.status == m.STATUS_REPLIED
    assert reloaded.replied_at != ""

    # Reply carries reply_to pointer and !reply label
    assert reply.reply_to == orig.id
    assert reply.label == "!reply"
    assert reply.from_ == "@me"


# ---------------------------------------------------------------------------
# Session-hygiene: send_message hooks on_done_delivered on !done label
# ---------------------------------------------------------------------------

def test_send_message_done_label_invokes_on_done_delivered(tmp_paths, monkeypatch):
    """send_message with label='!done' MUST call agents.on_done_delivered
    with the sender. Any other label MUST NOT."""
    from metasphere import agents as _agents

    calls: list[str] = []

    def fake_on_done(sender, paths=None):
        calls.append(sender)
        return None

    monkeypatch.setattr(_agents, "on_done_delivered", fake_on_done)

    m.send_message("@..", "!done", "done body", "@child-eph",
                   paths=tmp_paths, wake=False)
    m.send_message("@..", "!info", "info body", "@child-eph",
                   paths=tmp_paths, wake=False)
    m.send_message("@..", "!task", "task body", "@child-eph",
                   paths=tmp_paths, wake=False)

    assert calls == ["@child-eph"], (
        f"on_done_delivered must be called exactly once on !done, got {calls}"
    )


def test_send_message_done_hook_failure_does_not_break_delivery(tmp_paths, monkeypatch):
    """The session-hygiene hook is best-effort — any exception must NOT
    prevent the !done message from being delivered and indexed."""
    from metasphere import agents as _agents

    def boom(sender, paths=None):
        raise RuntimeError("simulated hook failure")

    monkeypatch.setattr(_agents, "on_done_delivered", boom)

    msg = m.send_message("@..", "!done", "still delivered", "@child-eph",
                         paths=tmp_paths, wake=False)
    assert msg.path is not None and msg.path.exists(), (
        "hook failure must not prevent message persistence"
    )
    loaded = m.read_message(msg.path)
    assert loaded.label == "!done"
    assert loaded.from_ == "@child-eph"


# ---------------------------------------------------------------------------
# wake_recipient_if_live — issue #106
# ---------------------------------------------------------------------------


def test_wake_recipient_if_live_uses_project_scoped_session(tmp_paths, monkeypatch):
    """Project-scoped agents have tmux sessions named
    ``metasphere-<project>-<agent>``. The legacy
    ``f'metasphere-{agent_name}'`` constructor missed those, silently
    dropping inbox-mediated wakes for every research / domain agent
    (issue #106).
    """
    from metasphere.agents import AgentRecord

    rec = AgentRecord(
        name="@brand-mentions",
        scope="",
        parent="",
        status="",
        spawned_at="",
        project="research",
    )
    monkeypatch.setattr("metasphere.session.list_agents", lambda: [rec])

    submitted: list[tuple[str, str]] = []

    def fake_submit(session, body, **kwargs):
        submitted.append((session, body))
        return True

    monkeypatch.setattr("metasphere.tmux.submit_to_tmux", fake_submit)

    ok = m.wake_recipient_if_live(
        "@brand-mentions", "!task", "@scheduler", "scan now",
        paths=tmp_paths,
    )

    assert ok is True
    assert submitted, "expected a tmux submit call"
    # The bug: bare ``metasphere-brand-mentions`` misses the project-scoped
    # session ``metasphere-research-brand-mentions``. With the fix the
    # wake routes to the project-aware name.
    assert submitted[0][0] == "metasphere-research-brand-mentions", (
        f"expected project-aware session name, got {submitted[0][0]!r}"
    )


def test_wake_recipient_if_live_returns_false_on_submit_failure(tmp_paths, monkeypatch):
    """When the tmux submit silently fails (session vanished mid-fire,
    defer-if-busy abort), the function must report False so callers can
    fall through to inbox-only delivery instead of stamping success
    (issue #106).
    """
    from metasphere.agents import AgentRecord

    rec = AgentRecord(
        name="@acme",
        scope="",
        parent="",
        status="",
        spawned_at="",
    )
    monkeypatch.setattr("metasphere.session.list_agents", lambda: [rec])
    monkeypatch.setattr(
        "metasphere.tmux.submit_to_tmux", lambda *a, **k: False,
    )

    ok = m.wake_recipient_if_live(
        "@acme", "!task", "@scheduler", "payload", paths=tmp_paths,
    )
    assert ok is False


def test_sandboxed_mark_done_never_reaches_real_tmux(tmp_paths, monkeypatch):
    """Regression — 2026-07-05: every run of this suite injected
    ``[wake] new !done from @worker: all green`` into the LIVE
    orchestrator pane on the host. ``mark_done`` replies via
    ``send_message`` with wake enabled, and ``wake_recipient_if_live``
    resolves tmux sessions globally — the ``paths=tmp_paths`` sandbox
    contains every file write and log_event, but not the tmux
    side-effect. ``_find_tmux``'s pytest guard is what keeps unmocked
    tests off the real server; this pins it by asserting no tmux
    subprocess fires from a sandboxed mark_done whose recipient has a
    resolvable session.
    """
    import subprocess as _sp

    from metasphere.agents import AgentRecord

    calls: list[list[str]] = []

    def spy_run(argv, **kw):
        calls.append([str(a) for a in argv])
        return _sp.CompletedProcess(argv, 1, "", "")

    monkeypatch.setattr("subprocess.run", spy_run)
    # Give the wake path a resolvable live-agent record so it proceeds
    # all the way to session resolution + submit (mirrors the incident:
    # the reply target @orchestrator always has a live session).
    rec = AgentRecord(
        name="@orchestrator", scope="", parent="", status="", spawned_at="",
    )
    monkeypatch.setattr("metasphere.session.list_agents", lambda: [rec])

    task_msg = m.send_message(
        "@orchestrator", "!task", "do the thing", "@worker",
        paths=tmp_paths, wake=False,
    )
    m.mark_done(task_msg.id, "all green", "@orchestrator", paths=tmp_paths)

    tmux_calls = [c for c in calls if c and "tmux" in Path(c[0]).name]
    assert not tmux_calls, (
        f"sandboxed mark_done reached a real tmux invocation: {tmux_calls}"
    )


# ---------------------------------------------------------------------------
# Fix #1 — !done auto-closes the dispatched task it references
# ---------------------------------------------------------------------------


def _new_task(tmp_paths, title: str = "ship me"):
    from metasphere import tasks as _tasks
    return _tasks.create_task(
        title, "!normal", tmp_paths.scope, tmp_paths.project_root,
        created_by="@orchestrator", assigned_to="@worker",
    )


def test_done_with_task_tag_in_body_closes_task(tmp_paths):
    from metasphere import tasks as _tasks
    t = _new_task(tmp_paths)
    assert _tasks._find_task_file(t.id, include_completed=False) is not None

    m.send_message(
        "@orchestrator", "!done", f"[task:{t.id}] shipped it", "@worker",
        paths=tmp_paths, wake=False,
    )

    # Auto-closed: gone from active/, archived as completed.
    assert _tasks._find_task_file(t.id, include_completed=False) is None
    closed = _tasks._find_task_file(t.id)
    assert closed is not None
    ct = _tasks.Task.from_text(closed.read_text(), path=closed)
    assert ct.status == _tasks.STATUS_COMPLETED
    assert "auto-closed via !done from @worker" in ct.body


def test_done_replying_to_task_message_closes_task(tmp_paths):
    from metasphere import tasks as _tasks
    t = _new_task(tmp_paths, "reply-close task")
    # Simulate the dispatch !task message (carries the [task:<id>] tag).
    task_msg = m.send_message(
        "@worker", "!task", f"[task:{t.id}] do the thing", "@orchestrator",
        paths=tmp_paths, wake=False,
    )
    # Worker marks it done — mark_done sends a !done with reply_to set.
    m.mark_done(task_msg.id, "all green", "@worker", paths=tmp_paths)

    assert _tasks._find_task_file(t.id, include_completed=False) is None
    assert _tasks._find_task_file(t.id) is not None


def test_done_without_tag_or_reply_does_not_close(tmp_paths):
    from metasphere import tasks as _tasks
    t = _new_task(tmp_paths, "untagged task")
    m.send_message(
        "@orchestrator", "!done", "done, but no task tag", "@worker",
        paths=tmp_paths, wake=False,
    )
    # No linkage → task stays open.
    assert _tasks._find_task_file(t.id, include_completed=False) is not None


def test_done_tag_for_already_closed_task_is_noop(tmp_paths):
    from metasphere import tasks as _tasks
    t = _new_task(tmp_paths, "double done task")
    _tasks.complete_task(t.id, "closed manually", tmp_paths.project_root)
    # A second !done with the tag must not raise or resurrect anything.
    m.send_message(
        "@orchestrator", "!done", f"[task:{t.id}] again", "@worker",
        paths=tmp_paths, wake=False,
    )
    assert _tasks._find_task_file(t.id, include_completed=False) is None


def test_sandboxed_escalation_cold_start_never_reaches_real_tmux(
    tmp_paths, monkeypatch,
):
    """Fast-follow to the 2026-07-05 wake leak (critic finding on PR #5):
    a high-priority send to a MISSION.md-backed agent whose soft wake
    fails escalates into ``wake_persistent`` — whose kill-session /
    new-session / send-keys path ran on ``agents._tmux_bin``, previously
    unguarded. One innocent test away from cold-starting a real claude
    REPL (or killing a real idle session that shares the name). Pin:
    the full escalation completes gracefully without ever exec'ing the
    host tmux binary.
    """
    import shutil as _shutil

    from metasphere.tmux import PYTEST_TMUX_SENTINEL

    real_tmux = _shutil.which("tmux")
    execs: list[str] = []

    def spy_run(argv, **kw):
        head = str(argv[0]) if argv else ""
        execs.append(head)
        if real_tmux and head == real_tmux:
            pytest.fail(f"sandboxed escalation exec'd the host tmux: {argv}")
        raise FileNotFoundError(head)

    monkeypatch.setattr("subprocess.run", spy_run)
    # Collapse _wait_for_ready's 1s polling sleeps.
    monkeypatch.setattr("metasphere.agents.time.sleep", lambda s: None)

    d = tmp_paths.agents / "@sleeper"
    d.mkdir(parents=True)
    (d / "MISSION.md").write_text("mission")
    (d / "scope").write_text(str(tmp_paths.project_root))

    # wake defaults to True; the guarded soft wake returns False, which
    # is exactly what trips the !task escalation into wake_persistent.
    msg = m.send_message(
        "@sleeper", "!task", "urgent thing", "@orchestrator",
        paths=tmp_paths,
    )

    # Inbox delivery must still have happened...
    assert msg.path is not None and msg.path.exists()
    # ...and every tmux exec attempt saw the sentinel, never the binary.
    tmux_execs = [h for h in execs if "tmux" in h]
    assert tmux_execs, "expected the escalation to attempt tmux calls"
    assert all(h == PYTEST_TMUX_SENTINEL for h in tmux_execs), tmux_execs


# ---------------------------------------------------------------------------
# Outbound activity feeds last_active (2026-07-05 21:05 @rage-lead incident)
# ---------------------------------------------------------------------------


def test_send_message_refreshes_sender_last_active(tmp_paths):
    """Sending a bus message refreshes the sender's ``last_active``
    sidecar — outbound activity IS activity for the reapers' shared
    idle signal. Incident pinned: an agent whose every wake hit
    "submit failed" for hours (so no input-side signal registered)
    was stale-killed 4 minutes after it SENT a !task."""
    from metasphere import agents

    d = tmp_paths.agents / "@quiet-worker"
    d.mkdir(parents=True)
    (d / "last_active").write_text("2026-07-01T00:00:00Z\n")

    m.send_message(
        "@orchestrator", "!task", "progress report", "@quiet-worker",
        paths=tmp_paths, wake=False,
    )

    idle = agents._last_active_idle_seconds(d)
    assert idle is not None and idle < 120, (
        f"send must refresh sender last_active; idle={idle}"
    )


def test_send_message_synthetic_sender_mints_no_ghost_dir(tmp_paths):
    """Synthetic senders (@consolidate, @heartbeat, @scheduler,
    @posthook, @user) have no agent dir and must not get one minted by
    the outbound-activity touch — a MISSION-less ghost dir would churn
    against the ephemeral GC (the touch_last_active docstring bug)."""
    for sender in ("@consolidate", "@heartbeat", "@scheduler",
                   "@posthook", "@user"):
        m.send_message(
            "@orchestrator", "!info", "tick", sender,
            paths=tmp_paths, wake=False,
        )
        assert not (tmp_paths.agents / sender).exists(), (
            f"ghost agent dir minted for synthetic sender {sender}"
        )
