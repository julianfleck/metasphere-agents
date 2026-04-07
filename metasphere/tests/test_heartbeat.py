"""Tests for metasphere.heartbeat."""

from __future__ import annotations

from pathlib import Path
from unittest import mock

import pytest

from metasphere import heartbeat as hb
from metasphere.messages import send_message
from metasphere.paths import Paths
from metasphere.tasks import create_task


def _agent(paths: Paths, name: str, status: str) -> Path:
    d = paths.agents / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "status").write_text(status, encoding="utf-8")
    (d / "scope").write_text(str(paths.repo), encoding="utf-8")
    return d


# ---------------------------------------------------------------------------
# check_urgent_messages
# ---------------------------------------------------------------------------


def test_check_urgent_messages_finds_unread_urgent(tmp_paths: Paths):
    send_message("@.", "!urgent", "fire", from_agent="@user", paths=tmp_paths, wake=False)
    send_message("@.", "!info", "calm", from_agent="@user", paths=tmp_paths, wake=False)
    found = hb.check_urgent_messages(tmp_paths)
    assert len(found) == 1
    assert found[0].label == "!urgent"
    assert found[0].body.strip() == "fire"


# ---------------------------------------------------------------------------
# check_blocked_agents
# ---------------------------------------------------------------------------


def test_check_blocked_agents_finds_waiting_and_blocked(tmp_paths: Paths):
    _agent(tmp_paths, "@a", "waiting: input")
    _agent(tmp_paths, "@b", "blocked: dep")
    _agent(tmp_paths, "@c", "active: working")
    found = {a.name for a in hb.check_blocked_agents(tmp_paths)}
    assert found == {"@a", "@b"}


# ---------------------------------------------------------------------------
# check_urgent_tasks
# ---------------------------------------------------------------------------


def test_check_urgent_tasks_counts_correctly(tmp_paths: Paths):
    create_task("urgent one", "!urgent", tmp_paths.scope, tmp_paths.repo)
    create_task("normal one", "!normal", tmp_paths.scope, tmp_paths.repo)
    create_task("urgent two", "!urgent", tmp_paths.scope, tmp_paths.repo)
    urgent, total = hb.check_urgent_tasks(tmp_paths)
    assert urgent == 2
    assert total == 3


# ---------------------------------------------------------------------------
# build_agent_context
# ---------------------------------------------------------------------------


def test_build_agent_context_prepends_header(tmp_paths: Paths):
    out = hb.build_agent_context("@orchestrator", tmp_paths)
    assert out.startswith("# HEARTBEAT")
    # Sections from build_context still present.
    assert "Metasphere Delta" in out
    assert "Messages" in out
    assert "Tasks" in out


# ---------------------------------------------------------------------------
# heartbeat_once dedupes via the state file
# ---------------------------------------------------------------------------


def test_heartbeat_once_dedupes_urgent_messages(tmp_paths: Paths):
    msg = send_message(
        "@.", "!urgent", "boom", from_agent="@user", paths=tmp_paths, wake=False
    )

    events: list[tuple] = []
    real = hb.log_event

    def fake_log(*args, **kwargs):
        events.append((args, kwargs))
        return real(*args, **kwargs)

    with mock.patch.object(hb, "log_event", side_effect=fake_log):
        hb.heartbeat_once(tmp_paths)
        first_calls = [
            e for e in events if e[0] and e[0][0] == "heartbeat.urgent_message"
        ]
        hb.heartbeat_once(tmp_paths)
        second_calls = [
            e for e in events if e[0] and e[0][0] == "heartbeat.urgent_message"
        ]

    assert len(first_calls) == 1
    # No new urgent_message log on the second tick — deduped.
    assert len(second_calls) == 1
    assert hb.already_notified(tmp_paths, f"urgent:{msg.id}")


# ---------------------------------------------------------------------------
# invoke_agent_heartbeat falls back to one-shot when no tmux session
# ---------------------------------------------------------------------------


def test_invoke_agent_heartbeat_falls_back_to_oneshot(tmp_paths: Paths):
    _agent(tmp_paths, "@orchestrator", "active")

    with mock.patch.object(hb, "session_alive", return_value=False), mock.patch.object(
        hb.subprocess, "run"
    ) as run:
        ok = hb.invoke_agent_heartbeat("@orchestrator", tmp_paths)

    assert ok is True
    assert run.called
    args, kwargs = run.call_args
    cmd = args[0]
    assert cmd[0] == "claude"
    assert "-p" in cmd
    assert "--allowedTools" in cmd


def test_log_status_to_disk_writes_marker(tmp_paths: Paths):
    hb.log_status_to_disk(tmp_paths)
    p = tmp_paths.state / "heartbeat_last_run"
    assert p.is_file()
    assert "alive at" in p.read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# scope normalization in daemon path
# ---------------------------------------------------------------------------


def test_heartbeat_daemon_normalizes_scope_to_repo(tmp_paths: Paths, monkeypatch):
    """Daemon must use paths.repo (env-resolved) not the cwd subdir.

    Simulates running the daemon from a deeply nested ``a/b/c`` subdir
    of the repo and asserts the per-tick :class:`Paths` carries the
    repo root, not the cwd.
    """
    nested = tmp_paths.repo / "a" / "b" / "c"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    captured: list[Paths] = []

    def fake_once(paths, invoke_agent=False):
        captured.append(paths)

    def fake_sleep(_seconds):
        raise StopIteration  # break out after one tick

    monkeypatch.setattr(hb, "heartbeat_once", fake_once)
    monkeypatch.setattr(hb.time, "sleep", fake_sleep)

    with pytest.raises(StopIteration):
        hb.heartbeat_daemon(interval_seconds=0)

    assert len(captured) == 1
    p = captured[0]
    assert p.repo == tmp_paths.repo
    assert p.repo != nested
