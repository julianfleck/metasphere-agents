"""Configuration loading from ~/.metasphere/config/*.env files.

Loads Telegram bot tokens and other configuration from
``~/.metasphere/config/*.env`` files.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from .paths import Paths, resolve

_ENV_LINE = re.compile(
    r"""^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$"""
)


def parse_env_file(path: Path) -> dict[str, str]:
    """Minimal POSIX-ish .env parser. Strips matched surrounding quotes."""
    out: dict[str, str] = {}
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        m = _ENV_LINE.match(line)
        if not m:
            continue
        k, v = m.group(1), m.group(2)
        if len(v) >= 2 and v[0] == v[-1] and v[0] in ('"', "'"):
            v = v[1:-1]
        out[k] = v
    return out


@dataclass
class TelegramConfig:
    bot_token: str | None = None
    chat_id: str | None = None
    rewrite_bot_token: str | None = None
    rewrite_chat_id: str | None = None


@dataclass
class Config:
    paths: Paths
    agent_id: str
    telegram: TelegramConfig = field(default_factory=TelegramConfig)
    extra: dict[str, str] = field(default_factory=dict)


def _pick(d: dict[str, str], *keys: str) -> str | None:
    for k in keys:
        if d.get(k):
            return d[k]
    return None


def load_config(paths: Paths | None = None) -> Config:
    """Load env files and return a typed Config snapshot."""
    from .identity import resolve_agent_id  # local import to avoid cycle

    paths = paths or resolve()
    cfg_dir = paths.config
    canonical = parse_env_file(cfg_dir / "telegram.env")
    rewrite = parse_env_file(cfg_dir / "telegram-rewrite.env")

    tg = TelegramConfig(
        bot_token=_pick(canonical, "TELEGRAM_BOT_TOKEN", "BOT_TOKEN"),
        chat_id=_pick(canonical, "TELEGRAM_CHAT_ID", "CHAT_ID"),
        rewrite_bot_token=_pick(rewrite, "TELEGRAM_BOT_TOKEN", "BOT_TOKEN"),
        rewrite_chat_id=_pick(rewrite, "TELEGRAM_CHAT_ID", "CHAT_ID"),
    )

    extra: dict[str, str] = {}
    if cfg_dir.exists():
        for env_file in sorted(cfg_dir.glob("*.env")):
            if env_file.name in ("telegram.env", "telegram-rewrite.env"):
                continue
            extra.update(parse_env_file(env_file))

    return Config(
        paths=paths,
        agent_id=resolve_agent_id(paths),
        telegram=tg,
        extra=extra,
    )
