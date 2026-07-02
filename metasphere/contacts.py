"""Unified ADDRESSBOOK loader.

Single source of truth for cross-channel contact handles (Telegram
chat ids, future: email, Slack ids, etc.). Lives at
``~/.metasphere/ADDRESSBOOK.yaml`` (instance state, deliberately
outside ``config/`` so it isn't conflated with token / settings env
files).

Schema::

    default-recipient: <name>      # optional; the "main user" fallback
    contacts:                      # outbound handles, keyed by contact name
      <name>:
        telegram: <chat_id>        # Telegram chat id (int)
    surfaces:                      # inbound reverse maps, PER-SURFACE + standalone
      slack:                       # (or per-bot: slack-<instance>)
        <user_id>: <name>          # e.g. U0123ABC: alice

Two SEPARATE concerns (the operator's per-surface decision — no unified identity):

* ``contacts`` — name → outbound handle (telegram chat id today). Used by
  :func:`lookup_contact` for ``message send --to <name>``.
* ``surfaces`` — surface handle → name, standalone. Used by
  :func:`reverse_lookup` to turn a raw inbound Slack uid into a friendly name
  for the envelope. A Slack uid is NOT attached to a telegram contact.
  Populated by ``metasphere addressbook sync-slack`` and the lazy
  ``users.info`` path; requires the ``users:read`` scope to resolve names
  (raw-uid fallback otherwise).

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
import tempfile
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
                        str(_legacy_contacts_path(paths))).get("contacts") or {}


def _load_default_recipient_name(paths: Paths | None = None) -> str | None:
    """Return the lowercase ``default-recipient`` name from
    ADDRESSBOOK.yaml, or ``None`` if not configured.

    Legacy ``telegram_contacts.json`` has no concept of
    default-recipient; that path always returns ``None``. Operators
    migrating off the legacy file get a populated default-recipient
    written by ``install.sh`` (see the migration block in install.sh).
    """
    return _load_cached(str(_addressbook_path(paths)),
                        str(_legacy_contacts_path(paths))).get("default-recipient")


@lru_cache(maxsize=4)
def _load_cached(addressbook_path: str, legacy_path: str) -> dict[str, Any]:
    """Cache key includes both paths so test fixtures don't bleed.

    Returns the parsed addressbook with shape::

        {
          "contacts": {<lower-name>: {<method>: <handle>, ...}, ...},
          "default-recipient": <lower-name> | None,
        }

    ``contacts`` keys are lowercased so case-insensitive lookup is
    a plain dict get.
    """
    ab_path = Path(addressbook_path)
    lc_path = Path(legacy_path)

    if ab_path.is_file():
        return _load_yaml(ab_path)

    if lc_path.is_file():
        _emit_legacy_warn(lc_path)
        return {"contacts": _load_legacy_json(lc_path),
                "default-recipient": None}

    _emit_missing_warn(ab_path)
    return {"contacts": {}, "default-recipient": None}


def _load_yaml(path: Path) -> dict[str, Any]:
    """Parse a single ADDRESSBOOK.yaml. Tolerates malformed YAML by
    returning empty + WARN."""
    empty: dict[str, Any] = {"contacts": {}, "default-recipient": None}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as e:
        print(f"[WARN] failed to read {path}: {e}", file=sys.stderr)
        return empty
    try:
        data = yaml.safe_load(raw) or {}
    except yaml.YAMLError as e:
        print(f"[WARN] malformed YAML at {path}: {e}", file=sys.stderr)
        return empty
    contacts = data.get("contacts") or {}
    if not isinstance(contacts, dict):
        print(
            f"[WARN] {path}: 'contacts' must be a mapping, got "
            f"{type(contacts).__name__}",
            file=sys.stderr,
        )
        contacts = {}
    out_contacts: dict[str, dict[str, Any]] = {}
    for name, methods in contacts.items():
        if not isinstance(methods, dict):
            continue
        out_contacts[str(name).lower()] = dict(methods)

    raw_default = data.get("default-recipient")
    default_name = str(raw_default).lower() if raw_default else None

    # Per-surface standalone reverse maps: surfaces.<surface>.<handle> -> name.
    # Kept SEPARATE from `contacts` (per the operator's per-surface decision — a slack
    # uid is NOT merged onto the telegram contact). Handles are stringified so
    # int / str shapes both match on reverse lookup.
    surfaces_raw = data.get("surfaces") or {}
    out_surfaces: dict[str, dict[str, str]] = {}
    if isinstance(surfaces_raw, dict):
        for surface, mapping in surfaces_raw.items():
            if not isinstance(mapping, dict):
                continue
            out_surfaces[str(surface)] = {
                str(handle): str(name) for handle, name in mapping.items()
            }

    return {
        "contacts": out_contacts,
        "default-recipient": default_name,
        "surfaces": out_surfaces,
    }


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


def _surface_type(surface_id: str) -> str:
    """Strip the per-instance suffix off ``surface_id``.

    Convention: ``surface_id`` is ``<type>[-<instance>]``, so the type
    is everything up to the first ``-``. ``telegram-relay`` →
    ``telegram``; ``slack-cluster-1`` → ``slack``; ``email`` → ``email``.
    """
    if not surface_id:
        return surface_id
    head, _, _ = surface_id.partition("-")
    return head


def lookup_contact(
    name: str,
    surface_id: str,
    paths: Paths | None = None,
) -> Any:
    """Resolve ``name`` to a handle for ``surface_id``, or ``None``.

    Two-step lookup: an exact ``surface_id`` key wins
    (``contacts.<name>.telegram-cluster-1``) so per-bot overrides are
    possible; otherwise fall back to the ``surface_type`` key
    (``contacts.<name>.telegram``), so existing addressbooks keep
    working unchanged.

    Returns the raw value from the YAML (typed by yaml.safe_load).
    Callers that need a typed value (e.g. a Telegram int chat id) coerce
    at their boundary; for Slack the value is a channel id string like
    ``"C12345"``, so no coercion fits all surfaces here.
    """
    contacts = load_addressbook(paths)
    entry = contacts.get(name.lower())
    if not entry:
        return None
    # Exact surface_id wins; fall back to surface_type prefix.
    if surface_id in entry:
        return entry[surface_id]
    surface_type = _surface_type(surface_id)
    return entry.get(surface_type)


def load_surface_map(paths: Paths | None = None) -> dict[str, dict[str, str]]:
    """Return the per-surface standalone reverse maps ``{surface: {handle: name}}``.

    Backs :func:`reverse_lookup`. These are deliberately separate from
    :func:`load_addressbook` — a Slack uid maps to a name STANDALONE, not by
    being attached to a telegram contact (the operator's per-surface decision). Empty
    dict when the addressbook has no ``surfaces`` section.
    """
    return _load_cached(str(_addressbook_path(paths)),
                        str(_legacy_contacts_path(paths))).get("surfaces") or {}


def reverse_lookup(
    handle: Any,
    surface_id: str,
    paths: Paths | None = None,
) -> str | None:
    """Reverse-map a surface ``handle`` (e.g. a Slack uid) to a name, or ``None``.

    Reads the STANDALONE per-surface map (``surfaces.<surface>.<handle> ->
    name``), NOT the telegram-keyed ``contacts`` section — per the operator's
    per-surface decision, a Slack uid is its own contact, never merged onto an
    existing telegram contact.

    Precedence: an exact ``surface_id`` key (``slack-explorer``) wins over the
    ``surface_type`` fallback (``slack``). Comparison is string-based so int /
    str handle shapes both match. Returns the name or ``None`` when unmapped —
    callers fall back to the raw handle.
    """
    if handle is None:
        return None
    target = str(handle)
    surfaces = load_surface_map(paths)
    exact = surfaces.get(surface_id) or {}
    if target in exact:
        return exact[target]
    surface_type = _surface_type(surface_id)
    by_type = surfaces.get(surface_type) or {}
    return by_type.get(target)


def lookup_telegram(name: str, paths: Paths | None = None) -> int | None:
    """Resolve ``name`` to a Telegram chat id, or ``None``.

    Lowercases ``name`` before lookup. Thin wrapper over
    :func:`lookup_contact` with ``surface_id="telegram"`` — keeps
    existing callers working unchanged.
    """
    chat_id = lookup_contact(name, "telegram", paths)
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


def default_telegram_chat_id(paths: Paths | None = None) -> int | None:
    """Resolve the configured "main user" Telegram chat id.

    Reads ``default-recipient: <name>`` from ADDRESSBOOK.yaml and
    returns that contact's ``telegram`` entry, or ``None`` if either
    the key is unset or the named contact has no telegram method.

    Callers must treat ``None`` as "no fallback configured" — the
    leak-vector fix requires we never silently substitute a
    last-inbound chat id. Operators migrating from the legacy
    ``telegram_contacts.json`` get ``default-recipient`` written by
    ``install.sh`` (instance state belongs in instance state).
    """
    name = _load_default_recipient_name(paths)
    if not name:
        return None
    return lookup_telegram(name, paths)


def set_surface_names(
    surface_key: str,
    uid_to_name: dict[str, str],
    paths: Paths | None = None,
) -> int:
    """Merge ``{uid: name}`` into the STANDALONE ``surfaces.<surface_key>`` map.

    Backs ``addressbook sync-slack`` (bulk) and the lazy ``users.info`` path
    (one uid). Slack contacts are keyed by uid → resolved name, standalone —
    NOT attached to telegram contacts. Existing entries for the same uid are
    overwritten (re-sync refreshes names); other surfaces / contacts are
    untouched.

    The whole file is rewritten atomically (YAML comments are not preserved —
    ADDRESSBOOK.yaml is instance state). Returns the count written. The process
    cache is cleared so later lookups see the new names.
    """
    clean = {
        str(uid): str(name)
        for uid, name in (uid_to_name or {}).items()
        if uid and name
    }
    if not clean:
        return 0

    paths = paths or resolve()
    ab_path = _addressbook_path(paths)
    data: dict[str, Any] = {}
    if ab_path.is_file():
        try:
            data = yaml.safe_load(ab_path.read_text(encoding="utf-8")) or {}
        except yaml.YAMLError:
            data = {}
    if not isinstance(data, dict):
        data = {}

    surfaces = data.get("surfaces")
    if not isinstance(surfaces, dict):
        surfaces = {}
    surface_map = surfaces.get(surface_key)
    if not isinstance(surface_map, dict):
        surface_map = {}
    surface_map.update(clean)
    surfaces[surface_key] = surface_map
    data["surfaces"] = surfaces

    ab_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{ADDRESSBOOK_BASENAME}.",
                              dir=str(ab_path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            yaml.safe_dump(data, f, sort_keys=False, allow_unicode=True)
        os.replace(tmp, ab_path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    clear_cache()
    return len(clean)


def clear_cache() -> None:
    """Reset the lru_cache. Used by tests to isolate fixtures."""
    _load_cached.cache_clear()
    global _LEGACY_WARN_EMITTED, _MISSING_WARN_EMITTED
    _LEGACY_WARN_EMITTED = False
    _MISSING_WARN_EMITTED = False
