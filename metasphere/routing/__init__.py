"""Cross-surface routing primitives.

The per-turn surface pin lives here: each inbound writes the
``(surface_id, chat_id)`` it arrived on to the target agent's
``active_conversation`` file, and outbound under ``--surface auto``
reads that file to pick a default surface.
"""

from .active import (
    ACTIVE_CONVERSATION_BASENAME,
    get_active_conversation,
    set_active_conversation,
)

__all__ = [
    "ACTIVE_CONVERSATION_BASENAME",
    "get_active_conversation",
    "set_active_conversation",
]
