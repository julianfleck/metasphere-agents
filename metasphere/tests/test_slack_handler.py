"""Tests for ``metasphere.slack.handler`` — DM / mention routing."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from metasphere.routing.active import get_active_conversation
from metasphere.slack import handler as _handler
from metasphere.paths import Paths


@pytest.fixture(autouse=True)
def _no_live_slack(monkeypatch):
    """Hermetic: never call live Slack for name resolution, and reset the
    per-process name cache + contacts cache between tests. Individual tests
    can re-patch ``resolve_user_name`` to exercise the lazy-success path."""
    from metasphere.slack import api as _api
    from metasphere import contacts as _contacts

    monkeypatch.setattr(_api, "resolve_user_name", lambda *a, **k: None)
    _handler._NAME_CACHE.clear()
    _contacts.clear_cache()
    yield
    _handler._NAME_CACHE.clear()
    _contacts.clear_cache()


@pytest.fixture
def stub_submit():
    return MagicMock(return_value=True)


@pytest.fixture
def _patch_archive(monkeypatch):
    """Stub the archiver call so we can assert it was invoked with
    ``surface_id`` without writing to the real stream dir."""
    calls: list = []

    def fake_archive(payload, base_dir=None, surface_id="telegram"):
        calls.append({
            "surface_id": surface_id,
            "channel": payload.get("chat", {}).get("id"),
            "text": payload.get("text"),
        })

    monkeypatch.setattr("metasphere.telegram.archiver.archive_message",
                        fake_archive)
    return calls


@pytest.fixture
def _patch_paths(tmp_paths: Paths, monkeypatch):
    """Make ``set_active_conversation`` write into tmp_paths."""
    from metasphere import paths as _paths_module

    monkeypatch.setattr(_paths_module, "resolve", lambda: tmp_paths)
    return tmp_paths


def _dm_event(text: str = "ping", channel: str = "D1", user: str = "U1",
              bot_id: str | None = None, subtype: str | None = None) -> dict:
    return {
        "type": "message",
        "channel_type": "im",
        "channel": channel,
        "text": text,
        "user": user,
        "ts": "1700000000.000100",
        "bot_id": bot_id,
        "subtype": subtype,
    }


def _mention_event(text: str = "@bot hello", channel: str = "C1",
                   user: str = "U1", bot_id: str | None = None) -> dict:
    return {
        "type": "app_mention",
        "channel": channel,
        "text": text,
        "user": user,
        "ts": "1700000001.000100",
        "bot_id": bot_id,
    }


def test_handle_dm_archives_with_surface_id(
    _patch_archive, _patch_paths, stub_submit, _patch_session,
):
    """A DM produces one archive call with the surface_id stamp, and routes
    into the target agent's OWN session (one-app-per-agent), not the
    orchestrator REPL."""
    ok = _handler.handle_dm(
        _dm_event(),
        surface_id="slack-relay",
        target_agent_id="@relay",
        tmux_submit=stub_submit,
    )
    assert ok is True
    assert len(_patch_archive) == 1
    assert _patch_archive[0]["surface_id"] == "slack-relay"
    assert _patch_archive[0]["channel"] == "D1"
    # Routed to @relay's own session via the route-to-session core, with
    # the process-nudge idiom (not the orchestrator default).
    stub_submit.assert_called_once()
    assert stub_submit.call_args.kwargs["session"] == "metasphere-relay"
    assert stub_submit.call_args.kwargs["escape_prefix"] is False


def test_handle_mention_archives_with_surface_id(
    _patch_archive, _patch_paths, stub_submit, _patch_session,
):
    ok = _handler.handle_mention(
        _mention_event(),
        surface_id="slack-cluster-1",
        target_agent_id="@cluster-1",
        tmux_submit=stub_submit,
    )
    assert ok is True
    assert len(_patch_archive) == 1
    assert _patch_archive[0]["surface_id"] == "slack-cluster-1"
    stub_submit.assert_called_once()
    assert stub_submit.call_args.kwargs["session"] == "metasphere-cluster-1"


def test_handle_own_bot_message_skipped(
    _patch_archive, _patch_paths, stub_submit,
):
    """A message with bot_id == self_bot_id is dropped — no recursion."""
    ok = _handler.handle_dm(
        _dm_event(bot_id="B999"),
        surface_id="slack-relay",
        target_agent_id="@relay",
        self_bot_id="B999",
        tmux_submit=stub_submit,
    )
    assert ok is False
    assert _patch_archive == []
    stub_submit.assert_not_called()


def test_handle_dm_skips_non_im_channel(
    _patch_archive, _patch_paths, stub_submit,
):
    """Defense-in-depth: a ``message`` event NOT in a DM is dropped."""
    evt = _dm_event()
    evt["channel_type"] = "channel"
    ok = _handler.handle_dm(
        evt, surface_id="slack-relay", target_agent_id="@relay",
        tmux_submit=stub_submit,
    )
    assert ok is False
    stub_submit.assert_not_called()


def test_handle_dm_skips_subtype_messages(
    _patch_archive, _patch_paths, stub_submit,
):
    """Edits / joins (``subtype`` set) are dropped until explicitly wanted."""
    evt = _dm_event(subtype="message_changed")
    ok = _handler.handle_dm(
        evt, surface_id="slack-relay", target_agent_id="@relay",
        tmux_submit=stub_submit,
    )
    assert ok is False
    stub_submit.assert_not_called()


def test_handle_dm_updates_active_conversation(
    _patch_archive, _patch_paths, stub_submit, _patch_session,
):
    """A DM writes the active_conversation pin for the target agent."""
    _handler.handle_dm(
        _dm_event(channel="D42"),
        surface_id="slack-relay",
        target_agent_id="@relay",
        tmux_submit=stub_submit,
    )
    pin = get_active_conversation("@relay", _patch_paths)
    assert pin is not None
    assert pin["surface_id"] == "slack-relay"
    assert pin["chat_id"] == "D42"


def test_handle_mention_does_not_pin_active_conversation(
    _patch_archive, _patch_paths, stub_submit, _patch_session,
):
    """LEAK FIX: a channel mention must NOT write the active_conversation pin —
    a shared channel can never become the agent's sticky default surface. The
    reply still routes via the explicit baked reply command (asserted
    separately in ``test_handle_mention_threads_surface_and_reply_command``)."""
    _handler.handle_mention(
        _mention_event(channel="C77"),
        surface_id="slack-cluster-1",
        target_agent_id="@cluster-1",
        tmux_submit=stub_submit,
    )
    assert get_active_conversation("@cluster-1", _patch_paths) is None


# --------------------------------------------------------------------------
# Slash command → route-to-session core (v1 ship)
# --------------------------------------------------------------------------

@pytest.fixture
def _patch_session(monkeypatch):
    """Safe session seams so handle_command never touches real tmux / spawns.

    Session 'alive' by default (no wake); tests flip it to exercise dormant.
    Returns the mocks for assertions.
    """
    from metasphere import agents as _agents
    from metasphere import session as _session

    state = {"alive": True}
    wake = MagicMock(return_value=(None, True))
    touch = MagicMock()
    monkeypatch.setattr(_agents, "session_alive", lambda name: state["alive"])
    monkeypatch.setattr(_agents, "wake_persistent", wake)
    monkeypatch.setattr(_agents, "touch_last_active", touch)
    monkeypatch.setattr(
        _session, "_resolve_session",
        lambda a: a if a.startswith("metasphere-") else f"metasphere-{a.lstrip('@')}",
    )
    return {"state": state, "wake": wake, "touch": touch}


def _command_payload(command="/demo", text="status?", channel="C100",
                     user_name="bob", user_id="U1"):
    return {
        "command": command,
        "text": text,
        "channel_id": channel,
        "user_name": user_name,
        "user_id": user_id,
    }


def test_handle_command_routes_to_target_session(
    _patch_archive, _patch_paths, stub_submit, _patch_session,
):
    """A literal /demo injects the request into @demo-agent's own session — the
    route-to-session core — WITHOUT writing the active_conversation pin (slash
    is channel-scoped; the reply rides the explicit baked command)."""
    ok = _handler.handle_command(
        _command_payload(text="status of the replica?"),
        surface_id="slack",
        resolver=lambda p, s: ("@demo-agent", "status of the replica?"),
        tmux_submit=stub_submit,
    )
    assert ok is True
    # LEAK FIX: slash does NOT pin the channel as the agent's default surface.
    assert get_active_conversation("@demo-agent", _patch_paths) is None
    stub_submit.assert_called_once()
    assert stub_submit.call_args.args[1] == "status of the replica?"
    assert stub_submit.call_args.kwargs["session"] == "metasphere-demo-agent"


def test_handle_command_injects_stripped_request_not_raw(
    _patch_archive, _patch_paths, stub_submit, _patch_session,
):
    """Canonical mode: the agent REPL sees only the request (selector token
    already stripped by the resolver), not the raw '/ms <agent> …' text."""
    ok = _handler.handle_command(
        _command_payload(command="/ms", text="demo-agent do the thing"),
        surface_id="slack",
        resolver=lambda p, s: ("@demo-agent", "do the thing"),
        tmux_submit=stub_submit,
    )
    assert ok is True
    assert stub_submit.call_args.args[1] == "do the thing"


def test_handle_command_sender_from_user_name(
    _patch_archive, _patch_paths, stub_submit, _patch_session,
):
    _handler.handle_command(
        _command_payload(user_name="bob", user_id="U1"),
        surface_id="slack",
        resolver=lambda p, s: ("@demo-agent", "hi"),
        tmux_submit=stub_submit,
    )
    assert stub_submit.call_args.args[0] == "@bob"


def test_handle_command_wakes_dormant_target(
    _patch_archive, _patch_paths, stub_submit, _patch_session,
):
    _patch_session["state"]["alive"] = False
    _handler.handle_command(
        _command_payload(),
        surface_id="slack",
        resolver=lambda p, s: ("@demo-agent", "status?"),
        tmux_submit=stub_submit,
    )
    _patch_session["wake"].assert_called_once_with("@demo-agent")
    _patch_session["touch"].assert_called_once_with("@demo-agent")
    stub_submit.assert_called_once()


def test_handle_command_alive_idle_target_process_nudge(
    _patch_archive, _patch_paths, stub_submit, _patch_session,
):
    """WAKE-ON-SEND: an alive-but-idle routed target must still be driven to
    PROCESS the delivered message. The submit goes through with the
    process-nudge idiom (``escape_prefix=False``) — NOT interrupt semantics —
    so the idle pane isn't knocked into Claude Code's rating / rewind modal
    (which ate the C-m and left the text queued, unsubmitted → no reply).

    The session is alive (default), so no cold-start wake fires, but the
    delivery submit still must run with ``escape_prefix=False``.
    """
    assert _patch_session["state"]["alive"] is True  # alive-but-idle
    ok = _handler.handle_command(
        _command_payload(text="ping?"),
        surface_id="slack",
        resolver=lambda p, s: ("@demo-agent", "ping?"),
        tmux_submit=stub_submit,
    )
    assert ok is True
    _patch_session["wake"].assert_not_called()  # alive → no cold-start
    stub_submit.assert_called_once()
    # The fix: routed delivery uses the wake/process-nudge idiom, never the
    # interrupt-Escape that races the idle-pane modal.
    assert stub_submit.call_args.kwargs["escape_prefix"] is False
    assert stub_submit.call_args.kwargs["session"] == "metasphere-demo-agent"


def test_deliver_to_orchestrator_path_unchanged(stub_submit):
    """The @orchestrator default-session path is untouched: it does NOT force
    ``escape_prefix=False`` (keeps interrupt semantics for user-inbound), and
    injects into the default session (``session=None``)."""
    ok = _handler._deliver_to_agent(
        "@orchestrator", "@bob", "hi there", tmux_submit=stub_submit,
    )
    assert ok is True
    stub_submit.assert_called_once()
    assert stub_submit.call_args.kwargs["session"] is None
    assert "escape_prefix" not in stub_submit.call_args.kwargs


def test_handle_command_no_agent_dropped(
    _patch_archive, _patch_paths, stub_submit, _patch_session,
):
    """A None target (unmapped / unknown agent) is dropped — the ephemeral ack
    already informed the user on the bolt thread."""
    ok = _handler.handle_command(
        _command_payload(command="/nope"),
        surface_id="slack",
        resolver=lambda p, s: (None, "no agent mapped for /nope"),
        tmux_submit=stub_submit,
    )
    assert ok is False
    stub_submit.assert_not_called()


# --------------------------------------------------------------------------
# Surface-aware envelope: surface_id + explicit baked reply command threaded
# through to the inject layer; telegram path unchanged; sender reverse-resolved.
# --------------------------------------------------------------------------

def test_handle_command_threads_surface_and_reply_command(
    _patch_archive, _patch_paths, stub_submit, _patch_session,
):
    """Slash inject carries surface_id + the explicit `slack send --channel`
    reply command (NOT --surface auto) down to the submit layer."""
    _handler.handle_command(
        _command_payload(text="status?", channel="C100"),
        surface_id="slack-explorer",
        resolver=lambda p, s: ("@demo-agent", "status?"),
        tmux_submit=stub_submit,
    )
    kw = stub_submit.call_args.kwargs
    assert kw["surface_id"] == "slack-explorer"
    assert "slack send --surface slack-explorer --channel C100" in kw["reply_command"]
    assert "--surface auto" not in kw["reply_command"]
    # the raw request is still what gets injected as the body
    assert stub_submit.call_args.args[1] == "status?"


def test_handle_dm_threads_surface_and_reply_command(
    _patch_archive, _patch_paths, stub_submit, _patch_session,
):
    _handler.handle_dm(
        _dm_event(text="hello", channel="D9"),
        surface_id="slack-relay",
        target_agent_id="@relay",
        tmux_submit=stub_submit,
    )
    kw = stub_submit.call_args.kwargs
    assert kw["surface_id"] == "slack-relay"
    assert "--channel D9" in kw["reply_command"]


def test_handle_mention_threads_surface_and_reply_command(
    _patch_archive, _patch_paths, stub_submit, _patch_session,
):
    _handler.handle_mention(
        _mention_event(text="@bot hi", channel="C77"),
        surface_id="slack-cluster-1",
        target_agent_id="@cluster-1",
        tmux_submit=stub_submit,
    )
    kw = stub_submit.call_args.kwargs
    assert kw["surface_id"] == "slack-cluster-1"
    assert "--channel C77" in kw["reply_command"]


def test_rendered_envelope_end_to_end(_patch_archive, _patch_paths, _patch_session):
    """With the REAL inject path (no stub), the tmux payload is the full
    self-describing slack envelope with the baked reply command."""
    captured = {}

    def fake_tmux(session, payload, **kw):
        captured["payload"] = payload
        return True

    import metasphere.tmux as _tmux
    import metasphere.telegram.inject as _inject
    orig = _inject._tmux_submit
    _inject._tmux_submit = fake_tmux
    try:
        _handler.handle_command(
            _command_payload(text="status?", channel="C0BC2EV7SFM"),
            surface_id="slack-explorer",
            resolver=lambda p, s: ("@demo-agent", "status?"),
        )
    finally:
        _inject._tmux_submit = orig

    payload = captured["payload"]
    assert payload.startswith("[")
    assert "via slack | reply: metasphere slack send" in payload
    assert "--channel C0BC2EV7SFM" in payload
    assert payload.endswith("status?")
    assert "telegram from" not in payload


def test_sender_reverse_resolved_from_addressbook(
    _patch_archive, _patch_paths, stub_submit, _patch_session,
):
    """An inbound uid present in the per-surface map renders the friendly name."""
    from metasphere import contacts as _contacts

    _contacts.set_surface_names("slack", {"U1": "bob"}, _patch_paths)
    _handler.handle_dm(
        _dm_event(text="hi", user="U1"),
        surface_id="slack-explorer",
        target_agent_id="@relay",
        tmux_submit=stub_submit,
    )
    assert stub_submit.call_args.args[0] == "@bob"


def test_sender_raw_uid_fallback_when_unmapped(
    _patch_archive, _patch_paths, stub_submit, _patch_session,
):
    """Unmapped uid + missing_scope (resolve_user_name stubbed None) → raw uid."""
    _handler.handle_dm(
        _dm_event(text="hi", user="U0UNKNOWN"),
        surface_id="slack-explorer",
        target_agent_id="@relay",
        tmux_submit=stub_submit,
    )
    assert stub_submit.call_args.args[0] == "@U0UNKNOWN"


def test_sender_lazy_resolve_persists_and_renders(
    _patch_archive, _patch_paths, stub_submit, _patch_session, monkeypatch,
):
    """When users.info resolves a name, it renders AND persists to the map."""
    from metasphere.slack import api as _api
    from metasphere import contacts as _contacts

    monkeypatch.setattr(_api, "resolve_user_name", lambda *a, **k: "alice")
    _handler.handle_dm(
        _dm_event(text="hi", user="U7"),
        surface_id="slack-explorer",
        target_agent_id="@relay",
        tmux_submit=stub_submit,
    )
    assert stub_submit.call_args.args[0] == "@alice"
    # persisted under surface_type 'slack' for future offline lookups
    assert _contacts.reverse_lookup("U7", "slack", _patch_paths) == "alice"
