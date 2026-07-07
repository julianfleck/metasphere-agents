"""Per-sender → agent routing + access gate for inbound Telegram.

By default Telegram is single-operator: every inbound wakes
``@orchestrator``. This module adds OPTIONAL per-sender routing so a
specific Telegram user can be bound to a specific agent (e.g. a given
user DMs the bot and reaches their own dedicated assistant), with an
optional access gate that DENIES unmapped senders instead of falling
through to the default agent.

Config lives HOST-SIDE ONLY at
``~/.metasphere/config/telegram-access.yaml`` (same posture as
``slack-commands.yaml`` / ``slack.env`` — never committed), read at
runtime per inbound so edits take effect on the next message with no
restart. Shape::

    default_agent: "@orchestrator"   # fallback for unmapped senders
    enforce: false                   # if true, unmapped senders are DENIED
    deny_message: "..."              # optional custom denial text
    users:                           # sender key -> agent
      "123456789": "@field-agent"      # numeric telegram chat/user id (preferred)
      alice: "@field-agent"             # or @username (lowercased, leading @ optional)

FAIL-OPEN: a missing file, malformed YAML, or non-mapping config yields
the historical behavior — ``(@orchestrator, allowed=True)`` for every
sender — so installing this module changes nothing until a config is
written. Numeric-id keys are matched BEFORE username keys (ids are
stable; usernames change and can be absent). In a private DM the Telegram
``chat_id`` equals the sender's user id, so keying on ``chat_id`` is the
reliable per-user handle.
"""

from __future__ import annotations

import logging
import os
from typing import Optional, Tuple

logger = logging.getLogger(__name__)

CONFIG_DIR = os.path.expanduser("~/.metasphere/config")
ACCESS_CONFIG_BASENAME = "telegram-access.yaml"
DEFAULT_AGENT = "@orchestrator"
DEFAULT_DENY_MESSAGE = (
    "You don't have access to an agent on this bot yet. "
    "Ask the operator to grant you access."
)

# (agent_id, allowed, deny_message_or_None). When allowed is True the
# agent should be woken + injected; when False the inbound is gated and
# deny_message (if any) is sent back to the sender instead.
Resolution = Tuple[str, bool, Optional[str]]


def _config_path(config_dir: Optional[str]) -> str:
    base = config_dir if config_dir is not None else CONFIG_DIR
    return os.path.join(base, ACCESS_CONFIG_BASENAME)


def load_access_config(config_dir: Optional[str] = None) -> dict:
    """Load ``telegram-access.yaml``.

    Returns ``{"default_agent": str, "enforce": bool,
    "deny_message": str | None, "users": dict[str, str]}``. A missing
    file, unreadable file, malformed YAML, or non-mapping document all
    yield the fail-open default (no users mapped, enforce off) rather
    than raising into the gateway poll loop. ``config_dir`` defaults to
    :data:`CONFIG_DIR` (read at call time so tests can point it at a tmp
    dir). Read on every inbound so edits take effect with no restart.
    """
    empty: dict = {
        "default_agent": DEFAULT_AGENT,
        "enforce": False,
        "deny_message": None,
        "users": {},
    }
    path = _config_path(config_dir)
    if not os.path.exists(path):
        return empty
    try:
        import yaml

        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:  # noqa: BLE001 — config error must not crash inbound
        logger.warning("telegram access config load failed (%s): %r", path, e)
        return empty
    if not isinstance(data, dict):
        logger.warning(
            "telegram access config %s is not a mapping; ignoring", path
        )
        return empty

    default_agent = str(data.get("default_agent") or DEFAULT_AGENT).strip()
    if not default_agent.startswith("@"):
        default_agent = "@" + default_agent

    enforce = bool(data.get("enforce", False))

    deny_message = data.get("deny_message")
    deny_message = str(deny_message) if deny_message else None

    users_raw = data.get("users") or {}
    users: dict[str, str] = {}
    if isinstance(users_raw, dict):
        for key, agent in users_raw.items():
            k = str(key).strip().lstrip("@").lower()
            target = str(agent).strip()
            if not k or not target:
                continue
            if not target.startswith("@"):
                target = "@" + target
            users[k] = target

    return {
        "default_agent": default_agent,
        "enforce": enforce,
        "deny_message": deny_message,
        "users": users,
    }


def resolve_target(
    chat_id: Optional[int],
    username: Optional[str],
    *,
    config_dir: Optional[str] = None,
) -> Resolution:
    """Resolve an inbound Telegram sender to a target agent + access decision.

    Returns ``(agent_id, allowed, deny_message)``:

    - mapped sender             → ``(their agent, True, None)``
    - unmapped, ``enforce`` off → ``(default_agent, True, None)`` — the
      historical single-operator behavior (unmapped → orchestrator).
    - unmapped, ``enforce`` on  → ``(default_agent, False, <deny text>)`` —
      gated: the caller sends the deny text and does NOT inject.

    Sender is matched by numeric ``chat_id`` first (stable; equals the
    user id in a private DM), then ``@username`` (lowercased, leading
    ``@`` stripped). With no config file every sender resolves to
    ``(@orchestrator, True, None)`` — installing this module is a no-op
    until a config is written.
    """
    cfg = load_access_config(config_dir)
    users: dict = cfg["users"]
    default_agent: str = cfg["default_agent"]

    if chat_id is not None:
        hit = users.get(str(chat_id).strip().lower())
        if hit:
            return hit, True, None
    if username:
        hit = users.get(username.strip().lstrip("@").lower())
        if hit:
            return hit, True, None

    if cfg["enforce"]:
        return default_agent, False, (cfg["deny_message"] or DEFAULT_DENY_MESSAGE)
    return default_agent, True, None
