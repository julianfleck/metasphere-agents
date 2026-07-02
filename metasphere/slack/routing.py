"""Agent-selection seam for inbound Slack events.

Which agent an inbound Slack event wakes is a pluggable policy (the routing
*selector* is still being chosen — per-app bot identity, in-message prefix, or
slash command). All policies converge on the same load-bearing core: route /
inject the request into the *target agent's own tmux session* (where its
persona MISSION/SOUL lives). Only *selection* differs, so selection lives
behind a single ``TargetResolver`` callable.

v1 ships the **slash-command** selector. One resolver handles three shapes,
checked in order — same first-token-selects, strip-before-inject mental model
as the prefix resolver:

- **canonical** (``/ms <agent> <request>``): first token selects the agent
  (validated against the LIVE roster), the remainder is the request.
- **literal** (``/demo <request>``): an explicit command→agent alias from
  config; the whole text is the request.
- **auto** (``/relay <request>``): ZERO-CONFIG — any slash command whose
  name matches a wakeable persistent agent (``/relay`` → ``@relay``)
  routes straight to it, whole text as the request. This is the simple path:
  register ``/<agent>`` in the Slack app and you're done; no config entry. The
  two explicit shapes are checked first, so config can still override/alias.

Concrete command names + any explicit command→agent map live HOST-SIDE ONLY in
``~/.metasphere/config/slack-commands.yaml`` (same posture as ``slack.env``),
read at runtime and never committed — this module is policy, not roster. The
auto shape needs NO config at all; ``canonical``/``literal`` remain for the
``/ms``-style single command and for aliasing a command to a differently-named
agent.

The resolver returns ``(target, request_text)``: ``target`` is the ``@agent``
to wake (or ``None`` when no agent is selected), and ``request_text`` is either
the request to inject (target set) or an ephemeral help/error message (target
``None``) for the slash ack.
"""

from __future__ import annotations

import logging
import os
from typing import Callable, Optional, Tuple

logger = logging.getLogger(__name__)

# (event_or_payload, surface_id) -> (target_agent | None, text). When target is
# set, text is the request to inject (selector token already stripped). When
# target is None, text is the ephemeral message to show the user (roster help
# / "no agent mapped").
ResolverResult = Tuple[Optional[str], str]
TargetResolver = Callable[[dict, str], ResolverResult]

CONFIG_DIR = os.path.expanduser("~/.metasphere/config")
COMMAND_CONFIG_BASENAME = "slack-commands.yaml"


def load_command_config(config_dir: Optional[str] = None) -> dict:
    """Load the slash-command config from ``slack-commands.yaml``.

    Shape::

        canonical_command: ms          # one registered command, agent-as-arg
        literal:                       # command-name -> agent direct aliases
          demo: "@demo-agent"

    Returns ``{"canonical_command": str | None, "literal": dict[str, str]}``.
    A missing file → empty config (no commands routed). Best-effort — a
    malformed file logs a warning and yields the empty config rather than
    breaking the websocket worker. ``config_dir`` defaults to :data:`CONFIG_DIR`
    (read at call time so tests can point it at a tmp dir).
    """
    empty: dict = {"canonical_command": None, "literal": {}}
    cfg = config_dir if config_dir is not None else CONFIG_DIR
    path = os.path.join(cfg, COMMAND_CONFIG_BASENAME)
    if not os.path.exists(path):
        return empty
    try:
        import yaml

        with open(path, encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
    except Exception as e:  # noqa: BLE001 — config error must not crash inbound
        logger.warning("slack command config load failed (%s): %r", path, e)
        return empty
    if not isinstance(data, dict):
        logger.warning("slack command config %s is not a mapping; ignoring", path)
        return empty

    canonical = data.get("canonical_command")
    canonical = str(canonical).lstrip("/").strip() if canonical else None

    literal_raw = data.get("literal") or {}
    literal: dict[str, str] = {}
    if isinstance(literal_raw, dict):
        for cmd, agent in literal_raw.items():
            name = str(cmd).lstrip("/").strip()
            target = str(agent).strip()
            if not name or not target:
                continue
            if not target.startswith("@"):
                target = "@" + target
            literal[name] = target
    return {"canonical_command": canonical, "literal": literal}


def _wakeable_agent_names() -> list[str]:
    """Sorted unique ``@<name>`` of every wakeable persistent agent.

    Source of truth for canonical-mode validation + roster help. Best-effort:
    a roster-walk error yields an empty list rather than raising into the
    websocket worker.
    """
    try:
        from .. import agents as _agents

        names = {
            rec.name for rec in _agents.list_agents() if rec.is_persistent
        }
        return sorted(names)
    except Exception as e:  # noqa: BLE001
        logger.warning("slack roster lookup failed: %r", e)
        return []


def _roster_help(prefix: str) -> str:
    roster = _wakeable_agent_names()
    listed = ", ".join(roster) if roster else "(none found)"
    return f"{prefix} Available agents: {listed}"


def slash_resolver(payload: dict, surface_id: str) -> ResolverResult:  # noqa: ARG001
    """SLASH selector — handles BOTH the canonical and literal command shapes.

    - **canonical** (``payload["command"]`` == ``canonical_command``):
      ``/ms <agent> <request>`` — first token is the agent (validated against
      the live roster), the remainder is the request. Returns
      ``("@<agent>", remainder)`` when valid; ``(None, <roster help>)`` when the
      agent is missing or unknown (so the ephemeral ack lists the roster).
    - **literal** (command in the ``literal`` map, e.g. ``/demo``):
      returns ``("@<agent>", full_text)``.
    - **auto** (command name == a wakeable agent, e.g. ``/relay``):
      zero-config — returns ``("@<cmd>", full_text)``.
    - unmapped command: ``(None, "no agent mapped for /<cmd>")``.
    """
    cmd = (payload.get("command") or "").lstrip("/").strip()
    text = (payload.get("text") or "").strip()
    cfg = load_command_config()
    canonical = cfg.get("canonical_command")
    literal = cfg.get("literal", {})

    if canonical and cmd == canonical:
        parts = text.split(None, 1)
        if not parts:
            return None, _roster_help(f"Usage: /{cmd} <agent> <request>.")
        token = parts[0].lstrip("@")
        remainder = parts[1].strip() if len(parts) > 1 else ""
        target = "@" + token
        if target not in _wakeable_agent_names():
            return None, _roster_help(f"Unknown agent '@{token}'.")
        return target, remainder

    if cmd in literal:
        return literal[cmd], text

    # Zero-config auto-route: a slash command whose NAME matches a wakeable
    # persistent agent routes straight to it (``/relay`` -> ``@relay``),
    # full text as the request. Register ``/<agent>`` in the Slack app and it
    # just works — no slack-commands.yaml entry needed. Canonical + literal are
    # checked first, so explicit config still overrides/aliases when present.
    if ("@" + cmd) in _wakeable_agent_names():
        return "@" + cmd, text

    return None, f"no agent mapped for /{cmd}"
