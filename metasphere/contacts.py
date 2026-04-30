"""Unified ADDRESSBOOK loader.

Single source of truth for cross-channel contact handles (Telegram
chat ids, future: email, Slack ids, etc.). Lives at
``~/.metasphere/ADDRESSBOOK.yaml`` (instance state, deliberately
outside ``config/`` so it isn't conflated with token / settings env
files).

Schema::

    contacts:
      <name>:
        telegram: <chat_id>
        # email: <addr>      # future
        # slack: <user_id>   # future

The file is:
- Optional. Stranger installs without it get an empty addressbook
  (no crash, one-time WARN to stderr).
- Cached for the process lifetime via ``functools.lru_cache``.
- Read-only via this module — operators edit the file directly.

Migration path: ``install.sh`` writes the addressbook from the
legacy ``~/.metasphere/config/telegram_contacts.json`` on install /
update if the new file doesn't already exist. The legacy file
remains as a fallback (with a one-time deprecation WARN at lookup
time) until the operator removes it.
"""

from __future__ import annotations

import json
import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from .paths import Paths, resolve

ADDRESSBOOK_BASENAME = "ADDRESSBOOK.yaml"
LEGACY_CONTACTS_BASENAME = "config/telegram_contacts.json"


def _addressbook_path(paths: Paths | None = None) -> Path:
    paths = paths or resolve()
    return paths.root / ADDRESSBOOK_BASENAME


def _legacy_contacts_path(paths: Paths | None = None) -> Path:
    paths = paths or resolve()
    return paths.root / LEGACY_CONTACTS_BASENAME


_LEGACY_WARN_EMITTED = False


def _emit_legacy_warn(legacy_path: Path) -> None:
    """Emit the one-time deprecation WARN to stderr."""
    global _LEGACY_WARN_EMITTED
    if _LEGACY_WARN_EMITTED:
        return
    _LEGACY_WARN_EMITTED = True
    print(
        f"[WARN] {legacy_path} is deprecated; "
        f"migrate to ~/.metasphere/{ADDRESSBOOK_BASENAME}",
        file=sys.stderr,
    )


_MISSING_WARN_EMITTED = False


def _emit_missing_warn(path: Path) -> None:
    """Emit one-time WARN when the addressbook is missing entirely."""
    global _MISSING_WARN_EMITTED
    if _MISSING_WARN_EMITTED:
        return
    _MISSING_WARN_EMITTED = True
    print(
        f"[WARN] {path} not found — "
        f"contact lookups will fall back to legacy "
        f"~/.metasphere/{LEGACY_CONTACTS_BASENAME} if present, "
        f"otherwise return empty.",
        file=sys.stderr,
    )


def load_addressbook(paths: Paths | None = None) -> dict[str, dict[str, Any]]:
    """Return the merged ``{name: {method: handle}}`` mapping.

    Resolution order:
    1. ``~/.metasphere/ADDRESSBOOK.yaml`` (canonical YAML).
    2. ``~/.metasphere/config/telegram_contacts.json`` (legacy JSON,
       wrapped under ``method='telegram'`` per entry, with a one-time
       deprecation WARN).

    Returns an empty dict when neither file is readable. Names are
    case-insensitive (lowercased on load).

    Result is cached for the process lifetime — operators editing
    the file at runtime should restart their CLI / daemon to pick up
    new entries.
    """
    return _load_cached(str(_addressbook_path(paths)),
                        str(_legacy_contacts_path(paths)))


@lru_cache(maxsize=4)
def _load_cached(addressbook_path: str, legacy_path: str) -> dict[str, dict[str, Any]]:
    """Cache key includes both paths so test fixtures don't bleed.

    Returns lowercase-keyed contacts dict. The keys are normalized so
    case-insensitive lookup is just a dict get on the lower form.
    """
    ab_path = Path(addressbook_path)
    lc_path = Path(legacy_path)

    if ab_path.is_file():
        return _load_yaml(ab_path)

    if lc_path.is_file():
        _emit_legacy_warn(lc_path)
        return _load_legacy_json(lc_path)

    _emit_missing_warn(ab_path)
    return {}


def _load_yaml(path: Path) -> dict[str, dict[str, Any]]:
    """Parse a single ADDRESSBOOK.yaml. Tolerates malformed YAML by
    returning empty + WARN."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"[WARN] failed to read {path}: {e}", file=sys.stderr)
        return {}
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as e:
        print(f"[WARN] malformed YAML at {path}: {e}", file=sys.stderr)
        return {}
    contacts = data.get("contacts") or {}
    if not isinstance(contacts, dict):
        print(
            f"[WARN] {path}: 'contacts' must be a mapping, got "
            f"{type(contacts).__name__}",
            file=sys.stderr,
        )
        return {}
    out: dict[str, dict[str, Any]] = {}
    for name, methods in contacts.items():
        if not isinstance(methods, dict):
            continue
        out[str(name).lower()] = dict(methods)
    return out


def _load_legacy_json(path: Path) -> dict[str, dict[str, Any]]:
    """Parse the legacy ``telegram_contacts.json`` (flat
    ``{name: chat_id}``) into the unified shape
    ``{name: {'telegram': chat_id}}``."""
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except (OSError, json.JSONDecodeError) as e:
        print(f"[WARN] failed to read legacy {path}: {e}", file=sys.stderr)
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict[str, Any]] = {}
    for name, chat_id in data.items():
        out[str(name).lower()] = {"telegram": chat_id}
    return out


def lookup_telegram(name: str, paths: Paths | None = None) -> int | None:
    """Resolve ``name`` to a Telegram chat id, or ``None``.

    Lowercases ``name`` before lookup.
    """
    contacts = load_addressbook(paths)
    entry = contacts.get(name.lower())
    if not entry:
        return None
    chat_id = entry.get("telegram")
    if chat_id is None:
        return None
    try:
        return int(chat_id)
    except (TypeError, ValueError):
        return None


def has_contact(name: str, paths: Paths | None = None) -> bool:
    """Return True iff ``name`` exists in the addressbook (any method)."""
    contacts = load_addressbook(paths)
    return name.lower() in contacts


def clear_cache() -> None:
    """Reset the lru_cache. Used by tests to isolate fixtures."""
    _load_cached.cache_clear()
    global _LEGACY_WARN_EMITTED, _MISSING_WARN_EMITTED
    _LEGACY_WARN_EMITTED = False
    _MISSING_WARN_EMITTED = False
