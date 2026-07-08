"""TelegramAdapter — :class:`SurfaceAdapter` wrapping the telegram poller.

Wraps :func:`metasphere.telegram.poller.run_poll_iteration` (inbound) and
:func:`metasphere.telegram.api.send_message` (outbound) so the gateway
daemon can drive telegram through the generic adapter contract.

This is purely structural: behavior matches the pre-adapter daemon loop
exactly. Per-update handler errors are still routed to ``log_event`` via
the same ``on_handler_error`` callback the daemon installed before.
"""

from __future__ import annotations

from typing import Callable, Optional

from ...telegram import api, poller
from ..adapter import SurfaceAdapter


class TelegramAdapter:
    """Adapter for Telegram bot transport.

    Args:
        on_handler_error: callback invoked when ``handle_update`` raises
            for a single update. Signature ``(update, exc) -> None``.
            Best-effort: the offset still advances so the failing update
            is not re-driven.
        surface_id: unique identifier for this Telegram bot instance.
            Defaults to ``"telegram"`` (the legacy single-bot identity).
            Additional bots (e.g. ``"telegram-cluster-2"``) pass their
            own id so per-bot token files + offset isolation key on it.
        target_agent_id: the agent inbound on THIS bot routes to, e.g.
            ``"@cluster-2"``. ``None`` (the legacy default) leaves routing
            to ``handle_update``'s own default (``@orchestrator``).
    """

    surface_type: str = "telegram"

    def __init__(
        self,
        on_handler_error: Optional[Callable[[poller.Update, Exception], None]] = None,
        surface_id: str = "telegram",
        target_agent_id: Optional[str] = None,
    ) -> None:
        self._on_handler_error = on_handler_error
        self.surface_id = surface_id
        self._target_agent_id = target_agent_id

    def receive(self, timeout: int = 1) -> int:
        return poller.run_poll_iteration(
            timeout=timeout,
            on_error=self._on_handler_error,
            surface_id=self.surface_id,
            target_agent_id=self._target_agent_id,
        )

    def send(self, chat_id: int, text: str) -> None:
        api.send_message(chat_id, text, surface_id=self.surface_id)


__all__ = ["TelegramAdapter", "SurfaceAdapter"]
