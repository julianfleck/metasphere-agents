"""Resolve the current agent identity.

Python port of scripts/metasphere-identity. Resolution order:
    1. $METASPHERE_AGENT_ID
    2. ~/.metasphere/current_agent  (whitespace-stripped)
    3. @orchestrator if ~/.metasphere/agents/@orchestrator exists
    4. @user
"""

from __future__ import annotations

import os

from .paths import Paths, resolve


def resolve_agent_id(paths: Paths | None = None) -> str:
    env = os.environ.get("METASPHERE_AGENT_ID")
    if env:
        return env.strip()

    paths = paths or resolve()

    pointer = paths.current_agent_file
    if pointer.is_file():
        try:
            val = pointer.read_text(encoding="utf-8").strip()
            if val:
                return val
        except OSError:
            pass

    if (paths.agents / "@orchestrator").is_dir():
        return "@orchestrator"

    return "@user"
