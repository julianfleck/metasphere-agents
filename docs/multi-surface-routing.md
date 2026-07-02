# Multi-surface routing

_Status: shipped 2026-06-19 in `feat/multi-surface-routing`. Slack
adapter ships separately in `feat/slack-adapter`; this doc covers
the surface-agnostic core that both Telegram and the upcoming Slack
adapter sit on._

## Why

A single agent identity (e.g. `@relay`) needs to be reachable
from more than one messaging surface. Slack is the natural choice
for workspace humans; Telegram covers everyone else. The harness
needed a surface-agnostic way to:

1. Tell adapters apart at runtime (two Telegram bots, two Slack
   workspaces, one agent identity in front of all of them).
2. Pin the surface a multi-message reply burst belongs on so we
   don't split a 3-message answer across Slack and Telegram.
3. Resolve `--surface auto` outbound to whichever surface the user
   most recently addressed the agent on.
4. Carry per-bot tokens + per-contact handles without forcing a
   global addressbook rewrite.

## The `(surface_id, chat_id)` tuple

Every inbound is identified by a 2-tuple:

- `surface_id` — unique per adapter instance. Convention:
  `<surface_type>[-<instance>]` (e.g. `telegram`,
  `telegram-relay`, `slack-cluster-1`). The leading
  `<surface_type>` is what an addressbook entry without a per-bot
  override matches against.
- `chat_id` — the surface-native conversation id (Telegram int,
  Slack channel id `"C12345"`, future: email address, …). Stored as
  a string so the routing layer doesn't have to know the per-surface
  type.

`SurfaceAdapter` (the gateway-daemon Protocol) now requires both
`surface_type: str` AND `surface_id: str`. `TelegramAdapter`
defaults `surface_id="telegram"` so single-bot installs keep working
unchanged.

## Per-turn surface pinning

When an inbound from `(surface_id, chat_id)` lands for agent
`@<id>`, the handler writes that tuple to
`~/.metasphere/agents/@<id>/active_conversation` (JSON, atomic via
`tempfile + os.replace`). The file shape:

```json
{
  "surface_id": "telegram-relay",
  "chat_id": "123456789",
  "ts": 1781821628.123
}
```

This is the **active conversation pin**. Outbound under
`--surface auto` reads the pin and sends to whichever surface the
user most recently wrote on. Cross-surface racing collapses to
most-recent-inbound wins (atomic file replace, last writer visible
on read).

API: `metasphere.routing.active.set_active_conversation` and
`get_active_conversation`. Missing pin → `None`; malformed pin
→ `None` (caller falls back to the legacy default rather than
crashing).

## CLI: `metasphere message send`

```
metasphere message send "<text>" [--surface auto|<id>]
                                 [--to <name>] [--chat-id <id>]
```

- `--surface auto` (default): read the agent's active_conversation
  pin and dispatch there. Missing pin → fall back to the legacy
  Telegram default-recipient with a one-line WARN on stderr.
- `--surface <id>`: explicit override (e.g. `--surface telegram-relay`).
  Wins over the pin.
- `--to <name>`: addressbook lookup (surface-aware — see below).
- `--chat-id <id>`: raw chat id; bypasses addressbook.

The calling agent's id is read from `METASPHERE_AGENT_ID`
(default `@orchestrator`).

The legacy CLI `metasphere telegram send` is preserved as a thin
wrapper that pins `surface_id="telegram"` — existing scripts keep
working. New code should prefer `metasphere message send`.

## Addressbook — surface-aware

`~/.metasphere/ADDRESSBOOK.yaml` gains optional per-surface keys:

```yaml
contacts:
  alice:
    telegram: 123456789                # surface_type fallback
    telegram-cluster-1: 999999         # exact surface_id override
    slack: U01ABC                      # surface_type fallback for slack
    slack-relay: C012XYZ           # exact surface_id override
```

`metasphere.contacts.lookup_contact(name, surface_id)`:

1. Try the exact `surface_id` key (`telegram-cluster-1`).
2. Fall back to the `surface_type` key (`telegram`).
3. Return `None`.

`lookup_telegram(name)` is preserved as a thin wrapper over
`lookup_contact(name, "telegram")` — int-coerced for back-compat.

## Per-agent token resolution

`metasphere/telegram/api.py:_load_token(surface_id=None)` looks up
the bot token in this order:

1. `TELEGRAM_BOT_TOKEN` env var.
2. `~/.metasphere/config/<surface_id>.env` (only when
   `surface_id` is set and isn't the legacy `"telegram"` default).
3. `~/.metasphere/config/telegram.env`.
4. `TELEGRAM_BOT_TOKEN_REWRITE` env var.
5. `~/.metasphere/config/telegram-rewrite.env`.

Per-bot env files belong at `~/.metasphere/config/<surface_id>.env`
with `TELEGRAM_BOT_TOKEN=<bot-token>`. Example:
`~/.metasphere/config/telegram-relay.env`.

## Adding a new surface adapter

Forward-pointer for the upcoming Slack work (PR2):

1. Implement `metasphere.gateway.adapter.SurfaceAdapter`:
   `surface_type: str`, `surface_id: str`, `receive(timeout) -> int`,
   `send(chat_id, text) -> None`.
2. On every inbound, call
   `metasphere.routing.active.set_active_conversation` so outbound
   `--surface auto` picks the surface up.
3. Stamp `surface_id` on every archived record so a single stream
   can carry multiple bots.
4. Plumb `surface_id` through your API client's token loader (same
   shape as `metasphere/telegram/api.py:_load_token`).
5. Wire the new surface_type into `metasphere.cli.message._dispatch`
   so `metasphere message send --surface <id>` routes through it.

The Slack adapter (see `docs/SETUP-slack.md`) follows this contract.

## Migration

Single-Telegram installs are unaffected:

- `AgentSpec.surfaces` defaults to `[]`.
- `TelegramAdapter.surface_id` defaults to `"telegram"`.
- `archiver.archive_message(...)` stamps `surface_id="telegram"`
  by default (legacy rows without the field are read identically;
  the renderer doesn't depend on it).
- `lookup_telegram` keeps its previous semantics.
- `metasphere telegram send` still works; only prints a one-line
  hint suggesting `metasphere message send` (suppress via
  `METASPHERE_SUPPRESS_TELEGRAM_DEPRECATION=1` if a script can't
  tolerate stderr noise).

Multi-surface installs:

- Set `surfaces: [telegram-relay, slack-relay]` in the
  agent's `templates/agents/<role>/config.md` (or operator override).
- Drop per-bot env files into `~/.metasphere/config/`.
- Add per-surface keys to ADDRESSBOOK.yaml as the operator wishes
  to override the surface_type defaults.
