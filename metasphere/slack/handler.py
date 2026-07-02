"""Slack inbound event handler.

Wires slack-bolt event handlers per app instance:

- ``message`` filtered to ``channel_type == "im"`` (DMs only)
- ``app_mention`` (explicit @-tags in channels)
- slash ``command`` (v1 ship: a registered slash command → its mapped agent;
  the command→agent map is host-side config, never committed)

Everything else is filtered out server-side via the app's Event
Subscriptions config (the operator sets the subscription to only
``message.im`` + ``app_mention``); the in-process filter here is
defense-in-depth so a misconfigured app doesn't leak channel chatter
into the agent's tmux REPL.

Each inbound:

1. Archives the raw event via the Telegram-style archiver (single
   JSONL stream, ``surface_id`` stamped onto the record).
2. For a **DM only**, writes the agent's active_conversation pin so
   outbound under ``--surface auto`` defaults back to that DM for the
   reply burst. Channel-scoped inbound (mention + slash) deliberately
   does NOT write the pin — a shared channel must never become an
   agent's sticky default surface (the stale-pin leak). The reply to a
   channel is always the EXPLICIT baked ``slack send --channel`` command,
   never a pin-resolved ``--surface auto`` send.
3. Delivers into the target agent's REPL. All three paths (DM, mention,
   slash) go through the **route-to-session core**
   (:func:`_deliver_to_agent`): resolve the agent → wake if dormant →
   inject into *that agent's own* session. For DM/mention the target is the
   surface's configured ``target_agent_id`` (one-app-per-agent); for slash
   it's the resolver's selection. When the target is ``@orchestrator`` the
   core keeps the orchestrator-REPL path, so the default surface is
   unchanged.

The own-bot filter (``bot_id == self_bot_id``) is on every handler so
the adapter doesn't recurse on its own outbound.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from .routing import TargetResolver, slash_resolver

logger = logging.getLogger(__name__)

# tmux_submit signature mirrors the Telegram handler.
TmuxSubmit = Callable[..., bool]


def _session_inject(
    sender: str, text: str, *, session: Optional[str] = None,
    defer_if_busy: bool = False, escape_prefix: bool = True,
    surface_id: str = "telegram", reply_command: Optional[str] = None,
) -> bool:
    """Inject into a SPECIFIC agent session (``None`` → orchestrator default).

    The session-aware injector used by :func:`_deliver_to_agent`; routed
    inbound needs to land in the resolved agent's own REPL, not the
    orchestrator's.

    ``escape_prefix`` mirrors :func:`metasphere.tmux.submit_to_tmux`. It
    defaults ``True`` (interrupt semantics — the orchestrator default path)
    but routed-agent delivery passes ``False`` so an *idle* recipient pane
    isn't knocked into Claude Code's rating / rewind modal by a stray
    Escape (the documented wake-idiom hazard in
    :func:`metasphere.agents._submit_via_tmux`).

    ``surface_id`` is threaded to :func:`submit_to_tmux` so the rendered
    envelope names the ACTUAL origin surface (``[via slack | …]``) instead of
    the hardcoded telegram prefix.
    """
    from ..telegram.inject import DEFAULT_SESSION, submit_to_tmux

    return submit_to_tmux(
        sender, text, session=session or DEFAULT_SESSION,
        defer_if_busy=defer_if_busy, escape_prefix=escape_prefix,
        surface_id=surface_id, reply_command=reply_command,
    )


def _reply_command(surface_id: str, channel: str) -> str:
    """The explicit, copy-verbatim reply command baked into a slack envelope.

    The operator wants the reply path EXPLICIT (not pin-resolved): the agent copies
    this command and fills in ``<reply>``. ``slack send`` already supports
    ``--surface``/``--channel``, so the origin surface + channel the handler
    holds are baked straight in. The ``active_conversation`` pin is still set
    (legacy / other consumers) but is no longer required for the reply path.

    For rich content — parens, bullets (•), backticks, ``$``, quotes,
    newlines — the appended hint steers the agent to the zero-quoting stdin
    form (``send ... - <<'EOF'``) so it never has to shell-escape the body
    (the recurring "eval: syntax error near unexpected token (" papercut).
    """
    return (
        f'metasphere slack send --surface {surface_id} '
        f'--channel {channel} "<reply>"  '
        f"— rich content? skip shell quoting: "
        f"metasphere slack send --surface {surface_id} --channel {channel} - "
        f"<<'EOF' … <reply> … EOF"
    )


# Per-process cache for the lazy users.info path: ``(surface_id, uid) -> name``.
# ``None`` is cached too so a missing_scope token isn't re-probed every inbound.
_NAME_CACHE: dict[tuple[str, str], Optional[str]] = {}


def _lazy_slack_name(handle: str, surface_id: str) -> Optional[str]:
    """users.info resolve for an unknown uid (cached + persisted). None if unresolved.

    On a hit, persists ``uid -> name`` into the standalone per-surface map so
    future inbound (and other agents) resolve it offline. LIVE-BLOCKED until the
    token gains ``users:read`` — returns None today, and the cached None
    prevents re-probing live Slack on every inbound.
    """
    key = (surface_id, handle)
    if key in _NAME_CACHE:
        return _NAME_CACHE[key]
    name: Optional[str] = None
    try:
        from . import api as _api

        name = _api.resolve_user_name(surface_id, handle)
        if name:
            from ..paths import resolve
            from .. import contacts as _contacts

            stype = surface_id.partition("-")[0].strip().lower() or "slack"
            _contacts.set_surface_names(stype, {handle: name}, resolve())
    except Exception as e:  # noqa: BLE001 — never block inbound
        logger.warning("slack lazy name resolve failed for %r: %r", handle, e)
    _NAME_CACHE[key] = name
    return name


def _resolve_sender(
    handle: Optional[str], surface_id: str, *, fallback: Optional[str] = None,
) -> str:
    """Friendly sender name for an inbound handle.

    Resolution order:
    1. ADDRESSBOOK reverse-lookup (``U0BC… → alice``) — offline, preferred.
    2. Lazy ``users.info`` (cached; live-blocked until ``users:read`` granted).
    3. ``fallback`` (e.g. the slash-provided ``user_name``), then the raw
       handle, then ``"slack-user"``.

    Best-effort — any resolution error degrades to the next step, never blocks
    inbound.
    """
    if handle:
        try:
            from ..paths import resolve
            from .. import contacts as _contacts

            name = _contacts.reverse_lookup(handle, surface_id, resolve())
            if name:
                return name
        except Exception as e:  # noqa: BLE001 — resolution must not block inbound
            logger.warning(
                "slack sender reverse-lookup failed for %r on %s: %r",
                handle, surface_id, e,
            )
        lazy = _lazy_slack_name(handle, surface_id)
        if lazy:
            return lazy
    return fallback or handle or "slack-user"


def _courtesy_send(surface_id: str, channel: str) -> Callable[[str], None]:
    """A send callback bound to a Slack ``(surface_id, channel)``.

    Used for the rate-limit courtesy reply so the notification lands on the
    SAME channel the inbound came from, via the existing ``slack send`` path.
    """
    def _send(text: str) -> None:
        from . import api as _api

        _api.send_message(surface_id, channel, text)

    return _send


def _deliver_to_agent(
    agent_id: str,
    sender: str,
    text: str,
    *,
    surface_id: str = "telegram",
    reply_command: Optional[str] = None,
    tmux_submit: Optional[TmuxSubmit] = None,
    defer_if_busy: bool = False,
    courtesy_send: Optional[Callable[[str], None]] = None,
) -> bool:
    """Route-to-session core: inject ``text`` into ``agent_id``'s own session.

    - ``@orchestrator`` keeps the unchanged default-session path
      (``submit_to_tmux`` already refreshes its ``last_active``), with
      interrupt semantics (``escape_prefix`` default).
    - Any other agent is resolved to its session via ``_resolve_session``
      (handles project-scoped ``metasphere-<project>-<agent>`` names); if that
      session is dead the agent is
      cold-started via ``wake_persistent`` before injecting, then
      ``last_active`` is touched so ``reap_dormant`` doesn't re-kill it.

    The delivery submit always uses the **process-nudge idiom**
    (``escape_prefix=False``) for routed agents — unconditionally, whether the
    session was dead-then-cold-started or already alive-but-idle. This is the
    wake-on-send fix: when the target session was alive but idle (agent woken
    earlier, sitting at the REPL), the old interrupt-semantics submit
    (``escape_prefix=True``) fired an Escape that knocked the idle pane into
    Claude Code's rating / rewind modal, so the paste + C-m raced the modal and
    the message sat *queued, unsubmitted* — the agent never processed it and no
    reply came back. ``escape_prefix=False`` is the same idiom
    :func:`metasphere.agents._submit_via_tmux` uses for wakes precisely because
    it submits cleanly on an idle pane (and Claude Code still queues-then-runs
    it if a turn is mid-flight).

    Reuses the canonical wake idiom (``_resolve_session`` + ``wake_persistent``)
    rather than reinventing a wake path.
    """
    submit = tmux_submit or _session_inject

    # Courtesy reply on rate-limit: if the target agent's pane shows a Claude
    # usage/rate limit it can't generate a reply — tell the inbound user once
    # (deduped per agent) on the origin channel via ``courtesy_send``. Probed
    # BEFORE any wake below: a dead/dormant session isn't rate-limited (it's
    # just asleep — waking it lets it answer); only an alive-but-limited pane
    # is. Best-effort — never blocks the inject.
    if courtesy_send is not None:
        try:
            from ..cli.failsafe import maybe_courtesy_reply
            from ..session import _resolve_session as _rs

            maybe_courtesy_reply(agent_id, _rs(agent_id), courtesy_send)
        except Exception:  # noqa: BLE001 — courtesy must not break delivery
            pass

    if agent_id == "@orchestrator":
        return bool(submit(
            sender, text, session=None, defer_if_busy=defer_if_busy,
            surface_id=surface_id, reply_command=reply_command,
        ))

    from .. import agents as _agents
    from ..session import _resolve_session

    session = _resolve_session(agent_id)
    if not _agents.session_alive(session):
        try:
            _agents.wake_persistent(agent_id)
        except Exception as e:  # noqa: BLE001 — wake failure must not lose inbound
            logger.warning(
                "slack deliver: wake_persistent(%s) raised: %r", agent_id, e,
            )
        session = _resolve_session(agent_id)  # record may resolve only post-wake

    ok = bool(submit(
        sender, text, session=session, defer_if_busy=defer_if_busy,
        escape_prefix=False, surface_id=surface_id, reply_command=reply_command,
    ))
    try:
        _agents.touch_last_active(agent_id)
    except Exception:  # noqa: BLE001 — best-effort, mirrors inject.py
        pass
    return ok


def _archive(event: dict, surface_id: str) -> None:
    """Stamp + archive the raw Slack event.

    The Telegram archive shape is dict-of-message; Slack event shape is
    different but the renderer only reads top-level fields, so we feed
    the raw event through with a ``surface_id`` stamp + a ``kind``
    marker that lets downstream readers distinguish slack rows. The
    archive call is wrapped in try/except so a Slack-side schema shift
    doesn't break injection.
    """
    try:
        from ..telegram import archiver
        # Adapt event shape to the renderer's expectations: ``from`` carries
        # the username, ``chat`` carries the channel id.
        payload: dict[str, Any] = {
            "kind": "slack",
            "from": {"username": event.get("user") or "(slack-user)"},
            "chat": {"id": event.get("channel")},
            "text": event.get("text") or "",
            "date": int(float(event.get("ts") or 0)) if event.get("ts") else 0,
            "ts": event.get("ts"),
            "event_type": event.get("type"),
            "channel_type": event.get("channel_type"),
        }
        archiver.archive_message(payload, surface_id=surface_id)
    except Exception as e:  # noqa: BLE001 — archive failure must not block inject
        logger.warning("slack archive failed for surface_id=%s: %r", surface_id, e)


def _pin_active(target_agent_id: str, surface_id: str, channel: str) -> None:
    try:
        from ..paths import resolve
        from ..routing.active import set_active_conversation

        set_active_conversation(target_agent_id, surface_id, channel, resolve())
    except Exception as e:  # noqa: BLE001
        logger.warning(
            "slack active_conversation pin failed for %s on %s: %r",
            target_agent_id, surface_id, e,
        )


def handle_dm(
    event: dict,
    *,
    surface_id: str,
    target_agent_id: str,
    self_bot_id: Optional[str] = None,
    tmux_submit: Optional[TmuxSubmit] = None,
) -> bool:
    """Process a ``message`` event filtered to DMs.

    Returns ``True`` if the event was routed (archived + injected),
    ``False`` if it was dropped (own-bot echo, missing fields, ...).
    """
    if not event:
        return False
    if event.get("channel_type") != "im":
        return False
    if event.get("bot_id") and event.get("bot_id") == self_bot_id:
        return False
    if event.get("subtype"):
        # Edits, joins, etc. — skip until they're explicitly wanted.
        return False
    text = event.get("text") or ""
    channel = event.get("channel") or ""
    if not channel:
        return False

    _archive(event, surface_id)
    _pin_active(target_agent_id, surface_id, channel)

    sender = f"@{_resolve_sender(event.get('user'), surface_id)}"
    _deliver_to_agent(
        target_agent_id, sender, text, surface_id=surface_id,
        reply_command=_reply_command(surface_id, channel),
        tmux_submit=tmux_submit, defer_if_busy=False,
        courtesy_send=_courtesy_send(surface_id, channel),
    )
    return True


def handle_mention(
    event: dict,
    *,
    surface_id: str,
    target_agent_id: str,
    self_bot_id: Optional[str] = None,
    tmux_submit: Optional[TmuxSubmit] = None,
) -> bool:
    """Process an ``app_mention`` event (explicit @-tag in a channel).

    A channel mention does NOT write the active_conversation pin: a shared
    channel must never become the agent's sticky default surface (the
    stale-pin leak). The reply still reaches the channel via the explicit
    baked ``slack send --channel`` command.
    """
    if not event:
        return False
    if event.get("bot_id") and event.get("bot_id") == self_bot_id:
        return False

    text = event.get("text") or ""
    channel = event.get("channel") or ""
    if not channel:
        return False

    _archive(event, surface_id)

    sender = f"@{_resolve_sender(event.get('user'), surface_id)}"
    _deliver_to_agent(
        target_agent_id, sender, text, surface_id=surface_id,
        reply_command=_reply_command(surface_id, channel),
        tmux_submit=tmux_submit, defer_if_busy=False,
        courtesy_send=_courtesy_send(surface_id, channel),
    )
    return True


def _archive_command(payload: dict, surface_id: str) -> None:
    """Archive a slash command, adapted to the Telegram-style event shape."""
    cmd = payload.get("command") or ""
    text = payload.get("text") or ""
    _archive(
        {
            "user": payload.get("user_id") or payload.get("user_name"),
            "channel": payload.get("channel_id"),
            "text": f"{cmd} {text}".strip(),
            "type": "slash_command",
        },
        surface_id,
    )


def handle_command(
    payload: dict,
    *,
    surface_id: str,
    resolver: Optional[TargetResolver] = None,
    tmux_submit: Optional[TmuxSubmit] = None,
) -> bool:
    """Route a slash ``command`` payload to its target agent's session.

    Resolves ``(target, request_text)`` via the resolver (default
    :func:`metasphere.slack.routing.slash_resolver`). A ``None`` target means
    no agent was selected (the ephemeral ack already showed the help text on
    the bolt thread) → drop. Otherwise deliver the **request_text** (selector
    token already stripped in canonical mode) into the agent's own session via
    the route-to-session core. Slash is stateless and channel-scoped, so it
    does NOT write the active_conversation pin (stale-pin leak); the reply goes
    to the channel via the explicit baked ``slack send --channel`` command.

    Returns ``True`` when delivered, ``False`` when dropped.
    """
    if not payload:
        return False
    resolve = resolver or slash_resolver
    target, request_text = resolve(payload, surface_id)
    if not target:
        logger.info(
            "slack command %r selected no agent (%s)",
            payload.get("command"), request_text,
        )
        return False
    channel = payload.get("channel_id") or ""
    if not channel:
        return False

    _archive_command(payload, surface_id)
    # Slash is stateless AND channel-scoped: no pin is written (a shared
    # channel must never become the agent's sticky default surface). The reply
    # path is the EXPLICIT `slack send --channel <id>` command baked into the
    # envelope below — the agent answers without ever consulting a pin.

    # Reverse-resolve the slack user id → friendly name (falls back to the
    # slash-provided user_name, then the raw id).
    sender = "@" + _resolve_sender(
        payload.get("user_id"), surface_id,
        fallback=payload.get("user_name") or payload.get("user_id"),
    )
    _deliver_to_agent(
        target, sender, request_text, surface_id=surface_id,
        reply_command=_reply_command(surface_id, channel),
        tmux_submit=tmux_submit,
        courtesy_send=_courtesy_send(surface_id, channel),
    )
    return True


def register_handlers(app, *, surface_id: str, target_agent_id: str,
                      self_bot_id: Optional[str] = None) -> None:
    """Wire ``handle_dm`` + ``handle_mention`` onto a slack-bolt ``App``.

    Kept separate from the handler functions so unit tests can exercise
    the routing logic without instantiating a full slack-bolt App.
    """
    @app.event("message")
    def _on_message(event, logger=None):  # noqa: ARG001 — bolt sig
        handle_dm(
            event,
            surface_id=surface_id,
            target_agent_id=target_agent_id,
            self_bot_id=self_bot_id,
        )

    @app.event("app_mention")
    def _on_mention(event, logger=None):  # noqa: ARG001
        handle_mention(
            event,
            surface_id=surface_id,
            target_agent_id=target_agent_id,
            self_bot_id=self_bot_id,
        )
