"""SlackAdapter — :class:`SurfaceAdapter` wrapping the Slack poller + api.

Each ``SlackAdapter`` instance owns ONE Slack app identity (one
``surface_id``); installs with multiple Slack bots register one
adapter per bot. The convention "agent identity is derived from
surface_id" — e.g. ``slack-relay`` → ``@relay`` — keeps
the mapping declarative.
"""

from __future__ import annotations

from ...slack import api as _slack_api
from ...slack import poller as _slack_poller
from ..adapter import SurfaceAdapter


def _derive_target_agent(surface_id: str) -> str:
    """Map ``surface_id`` → ``@<agent>``.

    Convention: strip the ``slack-`` prefix; the remainder is the
    agent id (``slack-relay`` → ``@relay``,
    ``slack-cluster-1`` → ``@cluster-1``). Operators that want a
    different mapping subclass this adapter and override.

    The bare legacy default (``slack``, the single-bot
    ``slack.env``) has no agent body, so it maps to ``@orchestrator``
    — inbound on it lands in the orchestrator REPL, matching the
    pre-route-to-session behaviour of the default surface. A
    per-agent bot is opted into by naming its config
    ``slack-<agent>.env``.
    """
    body = surface_id
    if body.startswith("slack-"):
        body = body[len("slack-"):]
    if body == "slack" or not body:
        return "@orchestrator"
    if not body.startswith("@"):
        body = "@" + body
    return body


class SlackAdapter:
    """Adapter for Slack bot transport.

    Args:
        surface_id: unique id for this bot instance, e.g.
            ``"slack-relay"``. Drives token lookup
            (``~/.metasphere/config/<surface_id>.env``) and the
            derived target agent id.
        target_agent_id: optional override for the derived mapping.
    """

    surface_type: str = "slack"

    def __init__(
        self,
        surface_id: str,
        target_agent_id: str | None = None,
    ) -> None:
        self.surface_id = surface_id
        self._target_agent_id = target_agent_id or _derive_target_agent(surface_id)

    def receive(self, timeout: int = 1) -> int:
        return _slack_poller.run_poll_iteration(
            self.surface_id,
            self._target_agent_id,
            timeout=timeout,
        )

    def send(self, chat_id: int | str, text: str) -> None:
        # Slack channel ids are strings; coerce for the Protocol contract.
        _slack_api.send_message(self.surface_id, str(chat_id), text)


__all__ = ["SlackAdapter", "SurfaceAdapter"]
