"""SurfaceAdapter — pluggable transport interface for the gateway daemon.

The gateway daemon historically called ``telegram.poller`` directly. This
module defines the Protocol that decouples the daemon loop from any one
surface (telegram, web chat, email, webhook) so additional transports can
be added without touching the daemon supervisor logic.

The Protocol is sync because the daemon's outer loop is sync — async
would force a redesign of session/watchdog/dormancy ticks. Adapters that
wrap async clients should run their own event loop internally and
present a sync ``receive`` to the daemon.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class SurfaceAdapter(Protocol):
    """Pluggable transport contract.

    Implementations connect a messaging surface (telegram, web chat, email,
    …) to the gateway daemon. The daemon calls :meth:`receive` once per
    poll tick; outbound traffic uses :meth:`send`.

    Attributes:
        surface_type: short identifier for logs/diagnostics (e.g. ``"telegram"``).
        surface_id: unique identifier for THIS adapter instance, e.g.
            ``"telegram"`` (the legacy single-bot default),
            ``"telegram-relay"``, or ``"slack-cluster-1"``. Used to
            disambiguate multiple adapters of the same ``surface_type``
            and to key per-bot token files under
            ``~/.metasphere/config/<surface_id>.env``.

    Notes:
        ``receive`` returns the count of inbound items processed in the
        tick — matches the existing ``poller.run_poll_iteration`` shape so
        the telegram path can wrap with no semantic change. A return of 0
        means "no traffic this tick"; raising propagates to the daemon's
        supervisor try/except (which logs and continues).
    """

    surface_type: str
    surface_id: str

    def receive(self, timeout: int = 1) -> int:
        """Poll the surface once. Return the number of inbound items handled."""
        ...

    def send(self, chat_id: int, text: str) -> None:
        """Dispatch ``text`` to ``chat_id`` on this surface."""
        ...
