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


#: Per-surface multi-bot config files (``telegram-field-agent.env``,
#: ``slack-field-agent.env``, ...) are intentionally EXCLUDED from the
#: blanket os.environ export below. Their whole purpose (see
#: ``gateway/daemon.py::_discover_telegram_surface_ids`` /
#: ``_discover_slack_surface_ids``) is to stay FILE-scoped so each bot's
#: token only reaches the adapter/CLI call that explicitly requests that
#: surface_id. Exporting them here would flatten every bot's credential
#: into one process-wide ``TELEGRAM_BOT_TOKEN``/``SLACK_BOT_TOKEN`` — on
#: a shared single-install-multi-agent deployment, the first such file
#: alphabetically silently becomes the DEFAULT token for every agent's
#: legacy (non-surface-aware) send call, letting one agent's messages go
#: out through another agent's bot unnoticed.
_SURFACE_SCOPED_PREFIXES = ("telegram-", "slack-")


def _is_surface_scoped_env_file(name: str) -> bool:
    if not name.startswith(_SURFACE_SCOPED_PREFIXES):
        return False
    # ``telegram-rewrite.env`` is a legacy exception: it's a real global
    # fallback token (see ``telegram/api.py::_load_token`` step 4/5), not
    # a per-agent bot surface — it's meant to be visible process-wide.
    return name not in ("telegram-rewrite.env",)


def load_env_to_environ(paths: Paths | None = None) -> int:
    """Export ``~/.metasphere/config/*.env`` keys into ``os.environ``.

    Reads every ``*.env`` file plus the bare ``env`` catch-all (a
    legacy extension-less file some installs use for API keys), EXCEPT
    per-surface multi-bot config files (see
    :data:`_SURFACE_SCOPED_PREFIXES` / :func:`_is_surface_scoped_env_file`)
    which must stay file-scoped. For each parsed key, calls
    ``os.environ.setdefault`` so values explicitly set in the process env
    take precedence — operators can override per-invocation via shell
    ``KEY=value pytest ...`` without editing the file.

    Returns the number of keys written. Idempotent: running it twice
    leaves the environment unchanged the second time.

    The function is called from ``metasphere/__init__.py`` at package
    import so any code path that touches the package (CLI, test
    fixtures, daemon) sees the operator's config-file values without
    a separate bootstrap step. Stranger installs without a config dir
    or with no ``*.env`` files are a clean no-op.
    """
    paths = paths or resolve()
    cfg_dir = paths.config
    if not cfg_dir.is_dir():
        return 0
    written = 0
    files = [
        f for f in cfg_dir.glob("*.env")
        if not _is_surface_scoped_env_file(f.name)
    ]
    bare = cfg_dir / "env"
    if bare.is_file():
        files.append(bare)
    for env_file in sorted(files):
        for k, v in parse_env_file(env_file).items():
            if k not in os.environ:
                os.environ[k] = v
                written += 1
    return written


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
