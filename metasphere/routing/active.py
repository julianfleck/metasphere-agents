"""Active-conversation pointer per agent.

Each inbound from ``(surface_id, chat_id)`` to agent ``@<id>`` writes
that tuple to ``~/.metasphere/agents/@<id>/active_conversation`` (JSON
file, atomic via ``tempfile + os.replace``). Outbound under
``--surface auto`` reads the file to pick a default destination so a
multi-message reply burst stays on whichever surface the user most
recently addressed the agent on.

The file shape is::

    {"surface_id": "telegram-relay",
     "chat_id": "228838013",
     "ts": 1781821628.123}

``chat_id`` is stored as a string so Telegram ints, Slack channel
ids (``"C12345"``), and email addresses fit without per-surface
casing. Callers convert back if they need ints.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from typing import Any, Optional

from ..paths import Paths

ACTIVE_CONVERSATION_BASENAME = "active_conversation"


def _agent_dir(agent_id: str, paths: Paths):
    """Resolve the agent's identity directory.

    Project-scoped agents win over global ones (matches
    ``Paths.find_agent_dir`` semantics). Falls back to
    ``paths.agent_dir`` so callers that pre-create the dir during seed
    can still write the pointer for an agent that hasn't received any
    inbound yet.
    """
    if not agent_id.startswith("@"):
        agent_id = "@" + agent_id
    found = paths.find_agent_dir(agent_id)
    return found if found is not None else paths.agent_dir(agent_id)


def set_active_conversation(
    agent_id: str,
    surface_id: str,
    chat_id: str | int,
    paths: Paths,
) -> None:
    """Pin ``(surface_id, chat_id)`` as the agent's current conversation.

    Atomic via ``tempfile + os.replace`` so a reader cannot observe
    a half-written file. The agent dir is created on demand — a
    fresh agent that has never received an inbound still gets a
    valid pointer when its first inbound lands.
    """
    if not agent_id.startswith("@"):
        agent_id = "@" + agent_id
    agent_dir = _agent_dir(agent_id, paths)
    agent_dir.mkdir(parents=True, exist_ok=True)
    target = agent_dir / ACTIVE_CONVERSATION_BASENAME
    payload = {
        "surface_id": str(surface_id),
        "chat_id": str(chat_id),
        "ts": time.time(),
    }
    fd, tmp = tempfile.mkstemp(
        prefix=f".{ACTIVE_CONVERSATION_BASENAME}.",
        dir=str(agent_dir),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, target)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def get_active_conversation(
    agent_id: str,
    paths: Paths,
) -> Optional[dict[str, Any]]:
    """Return the agent's active conversation pin, or ``None``.

    Missing file → ``None``. Malformed JSON or missing required keys
    → ``None`` (defensive; the caller falls back to legacy default
    rather than crashing on a corrupt pointer).
    """
    if not agent_id.startswith("@"):
        agent_id = "@" + agent_id
    agent_dir = _agent_dir(agent_id, paths)
    target = agent_dir / ACTIVE_CONVERSATION_BASENAME
    if not target.is_file():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    if "surface_id" not in data or "chat_id" not in data:
        return None
    return data
