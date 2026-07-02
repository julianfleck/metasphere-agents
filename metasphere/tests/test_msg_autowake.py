"""Tests for the high-priority send-wake escalation (B3).

When the best-effort tmux inject in ``wake_recipient_if_live`` returns
``delivered=False`` for a !task / !urgent / !query message,
:func:`metasphere.messages.send_message` escalates to
:func:`metasphere.agents.wake_persistent` so the recipient's session
gets cold-started (or the inject retried) instead of leaving the
message stranded in the inbox. Regression target: a !task that
sat 7h unread on a nominally-alive but unresponsive session before
the operator noticed.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

from metasphere import messages as m
from metasphere.paths import Paths


def _make_persistent(tmp_paths: Paths, name: str) -> Path:
    """Create a minimal persistent-agent dir (MISSION.md is the marker
    that ``wake_persistent`` checks). Matches the helper in
    test_wake_banner_truncation.py."""
    d = tmp_paths.agents / name
    d.mkdir(parents=True, exist_ok=True)
    (d / "MISSION.md").write_text("mission")
    (d / "scope").write_text(str(tmp_paths.project_root))
    return d


# ---------------------------------------------------------------------------
# Positive escalation: high-priority labels to a dormant agent fire
# wake_persistent.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", ["!task", "!urgent", "!query"])
def test_high_priority_dormant_escalates(tmp_paths, label):
    _make_persistent(tmp_paths, "@probe")
    calls: list[tuple[str, str | None]] = []

    def fake_wake_persistent(agent_name, first_task=None, paths=None, **_kw):
        calls.append((agent_name, first_task))
        return MagicMock(), True

    with patch.object(m, "wake_recipient_if_live", return_value=False), \
            patch("metasphere.agents.wake_persistent",
                  side_effect=fake_wake_persistent) as wp:
        m.send_message(
            "@probe", label, "body of task", "@sender", paths=tmp_paths,
        )

    assert wp.called, f"expected escalation for {label} to dormant probe"
    assert calls[0][0] == "probe"
    # First-task pointer must reference the message id we just wrote
    # (msg id format: msg-<unix-ms>-<rand>). No double prefixing:
    # the literal text contains exactly one "msg-" segment.
    assert "New " + label + " in inbox: msg-" in calls[0][1]
    # Regression guard: msg_id already begins with "msg-", so the
    # pointer must NOT produce "msg-msg-" (caught in the 20:34Z
    # probe smoke).
    assert "msg-msg-" not in calls[0][1]


def test_alive_recipient_no_escalation(tmp_paths):
    _make_persistent(tmp_paths, "@probe")
    with patch.object(m, "wake_recipient_if_live", return_value=True), \
            patch("metasphere.agents.wake_persistent") as wp:
        m.send_message(
            "@probe", "!task", "body", "@sender", paths=tmp_paths,
        )
    assert not wp.called, "should not escalate when initial wake delivered"


# ---------------------------------------------------------------------------
# Negative: heartbeat-cadence labels never escalate, even when delivered
# came back False.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("label", ["!info", "!done", "!reply"])
def test_heartbeat_labels_never_escalate(tmp_paths, label):
    _make_persistent(tmp_paths, "@probe")
    with patch.object(m, "wake_recipient_if_live", return_value=False), \
            patch("metasphere.agents.wake_persistent") as wp:
        m.send_message(
            "@probe", label, "body", "@sender", paths=tmp_paths,
        )
    assert not wp.called, f"{label} must stay on heartbeat cadence"


# ---------------------------------------------------------------------------
# Guards: self-send, project recipient, @user, wake=False all suppressed.
# ---------------------------------------------------------------------------


def test_self_send_no_escalation(tmp_paths):
    # An agent !tasking itself must not bootstrap-loop its own session.
    _make_persistent(tmp_paths, "@probe")
    with patch.object(m, "wake_recipient_if_live", return_value=False), \
            patch("metasphere.agents.wake_persistent") as wp:
        m.send_message(
            "@probe", "!task", "body", "@probe", paths=tmp_paths,
        )
    assert not wp.called


def test_project_recipient_no_escalation(tmp_paths):
    # ``testproj`` is registered by the tmp_paths fixture, so
    # @testproj resolves to a project, not an agent. Projects don't
    # have tmux sessions.
    with patch.object(m, "wake_recipient_if_live", return_value=False), \
            patch("metasphere.agents.wake_persistent") as wp:
        m.send_message(
            "@testproj", "!task", "body", "@sender", paths=tmp_paths,
        )
    assert not wp.called


def test_user_sender_no_escalation(tmp_paths):
    # ``from_agent == "@user"`` short-circuits both the initial wake
    # AND the escalation — user-side sends never trigger pane inject.
    _make_persistent(tmp_paths, "@probe")
    with patch.object(m, "wake_recipient_if_live", return_value=False) as wr, \
            patch("metasphere.agents.wake_persistent") as wp:
        m.send_message(
            "@probe", "!task", "body", "@user", paths=tmp_paths,
        )
    assert not wr.called
    assert not wp.called


def test_wake_false_no_escalation(tmp_paths):
    # ``wake=False`` is the schedule/heartbeat path that writes the
    # message but stays silent. Escalation must respect the same
    # disable bit.
    _make_persistent(tmp_paths, "@probe")
    with patch.object(m, "wake_recipient_if_live") as wr, \
            patch("metasphere.agents.wake_persistent") as wp:
        m.send_message(
            "@probe", "!task", "body", "@sender",
            paths=tmp_paths, wake=False,
        )
    assert not wr.called
    assert not wp.called


# ---------------------------------------------------------------------------
# Robustness: wake_persistent raising ValueError (target not a
# registered persistent agent) must NOT crash send_message — inbox
# delivery already succeeded before the escalation runs.
# ---------------------------------------------------------------------------


def test_wake_persistent_value_error_swallowed(tmp_paths):
    # Create a persistent dir so the explicit gate passes, then patch
    # wake_persistent to raise — covers the race where the agent dir
    # was reaped between the gate and the call.
    _make_persistent(tmp_paths, "@probe")
    with patch.object(m, "wake_recipient_if_live", return_value=False), \
            patch("metasphere.agents.wake_persistent",
                  side_effect=ValueError("not persistent")):
        msg = m.send_message(
            "@probe", "!task", "body", "@sender", paths=tmp_paths,
        )
    # send_message returned normally; the message is on disk.
    assert msg.id.startswith("msg-")
    assert msg.path is not None and msg.path.exists()


def test_wake_persistent_other_exception_logged_not_raised(tmp_paths, caplog):
    _make_persistent(tmp_paths, "@probe")
    with patch.object(m, "wake_recipient_if_live", return_value=False), \
            patch("metasphere.agents.wake_persistent",
                  side_effect=RuntimeError("tmux gone")):
        msg = m.send_message(
            "@probe", "!task", "body", "@sender", paths=tmp_paths,
        )
    assert msg.path is not None and msg.path.exists()


# ---------------------------------------------------------------------------
# _is_wakeable_agent_target unit coverage.
# ---------------------------------------------------------------------------


def test_is_wakeable_agent_target_filters(tmp_paths):
    _make_persistent(tmp_paths, "@probe")
    assert m._is_wakeable_agent_target("@probe", tmp_paths) is True
    assert m._is_wakeable_agent_target("@user", tmp_paths) is False
    assert m._is_wakeable_agent_target("@..", tmp_paths) is False
    assert m._is_wakeable_agent_target("@.", tmp_paths) is False
    assert m._is_wakeable_agent_target("@/sub", tmp_paths) is False
    assert m._is_wakeable_agent_target("plainstring", tmp_paths) is False
    assert m._is_wakeable_agent_target("@", tmp_paths) is False
    # Registered project name → not an agent.
    assert m._is_wakeable_agent_target("@testproj", tmp_paths) is False


def test_high_priority_constant_is_exact_set():
    # Strict superset of the original {!task, !urgent} brief plus
    # !query per a later amendment to the original directive. No drift.
    assert m.HIGH_PRIORITY_LABELS == frozenset({"!task", "!urgent", "!query"})
