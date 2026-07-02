# Slack adapter — operator setup

_Live Slack wiring is operator-driven: the code ships ready, but you
must pick a workspace and create the Slack apps. This guide covers
bringing one or more bots online. It uses two example bots throughout
— `slack-<name>` routing to agent `@<name>` — but the steps are the
same for any number of bots; repeat per bot._

## 0. Decide where the Slack workspace lives

The workspace can be:

- An existing workspace you're already in (e.g. a personal or
  client workspace).
- A new workspace at `https://slack.com/get-started`.

This is the one upstream decision the code can't make. Pick before
proceeding.

## 1. Per-bot: create the Slack app

For each bot you want to bring online, create one Slack app. The two
example bots below are:

- `slack-relay` → routes to `@relay`
- `slack-cluster-1` → routes to `@cluster-1`

Per bot, repeat:

1. Go to <https://api.slack.com/apps> → **Create New App** → **From scratch**.
   - App Name: e.g. `relay-bot` (or `cluster-1-bot`).
   - Workspace: the one picked in step 0.
2. **Socket Mode** (sidebar) → enable.
   - Generate an **App-Level Token** with `connections:write` scope.
     Save the `xapp-…` value — that's the `SLACK_APP_TOKEN`.
3. **OAuth & Permissions** → **Scopes** → **Bot Token Scopes**, add:
   - `chat:write` — outbound `chat.postMessage`.
   - `im:history` — read DM threads.
   - `im:read` — receive DM events.
   - `im:write` — open DM channels.
   - `app_mentions:read` — receive `app_mention` events.
   - `files:write` — `send_document` via `files_upload_v2`.
   - `users:read` — *(optional, recommended)* resolve inbound user
     ids → friendly names for the inbound envelope (``addressbook
     sync-slack`` + lazy ``users.info``). Without it, inbound renders
     the raw uid (``U0BC…``) instead of a name — no failure, just less
     legible. Add the scope **and reinstall** the app to enable.
4. **Event Subscriptions** → **Subscribe to bot events**:
   - `message.im` (DMs only — `channel_type=="im"` filter is also
     applied client-side as defense-in-depth).
   - `app_mention` (explicit @-tags in channels).
   - **Nothing else.** This is the server-side noise filter.
5. **App Home** (sidebar) → **Show Tabs** → enable the **Messages
   Tab**, and check **Allow users to send Slash commands and messages
   from the messages tab**. Without this, DMs to the bot silently fail
   — Slack shows "Sending messages to this app has been turned off" /
   "Couldn't load thread" with no inbound event reaching the daemon.
   Do this **before** installing/copying the token below.
   - ⚠️ Toggling this after install **wedges the desktop DM view** — a
     full Slack app restart (quit + reopen, not just reload) is needed
     to clear the cached "turned off" state.
6. **Slash Commands** (sidebar) → **Create New Command**, one per agent you
   want reachable: Command = `/<agent>` (e.g. `/relay`), any description,
   Request URL can be a placeholder (Socket Mode delivers the command — no URL
   needed). This is the **zero-config route**: a `/<agent>` whose name matches a
   wakeable persistent agent is routed straight to `@<agent>` with the full text
   as the request — nothing to add in `slack-commands.yaml`. Only use that file
   for the `/ms <agent>`-style single command (`canonical_command`) or to alias
   a command to a differently-named agent (`literal`); see
   `docs/slack-commands.example.yaml`.
7. **Install to Workspace** → copy the **Bot User OAuth Token**
   (`xoxb-…`). That's the `SLACK_BOT_TOKEN`. (Re-install after adding slash
   commands so they register.)

## 2. Drop token files

One env file per bot, under `~/.metasphere/config/`:

```
~/.metasphere/config/slack-relay.env
~/.metasphere/config/slack-cluster-1.env
```

Shape (one per bot):

```ini
SLACK_BOT_TOKEN=xoxb-...
SLACK_APP_TOKEN=xapp-...
```

Mode `600` (token files are sensitive):

```
chmod 600 ~/.metasphere/config/slack-*.env
```

## 3. Add surfaces to the agent specs

Each agent identity (`@relay`, `@cluster-1`) needs to exist as a
spec. Drop a `surfaces` list into each spec's `config.md`:

```yaml
---
name: relay
role: lead
surfaces: [telegram-relay, slack-relay]
---
```

The `surfaces` field is declarative — it documents which adapters an
agent is reachable on. The gateway constructs adapters from the
discovered token files (see §6), not from this list.

## 4. Drop matching env files for Telegram bots

Each Telegram bot also gets its own env file under the same path:

```
~/.metasphere/config/telegram-relay.env
~/.metasphere/config/telegram-cluster-1.env
```

Shape:

```ini
TELEGRAM_BOT_TOKEN=...
```

## 5. Optional: addressbook entries

If you want `metasphere message send --to <name>` to resolve to a
per-surface handle, add per-surface keys to
`~/.metasphere/ADDRESSBOOK.yaml`:

```yaml
contacts:
  alice:
    telegram: 100000001
    telegram-cluster-1: 100000001
    slack: U01ABC                # surface_type fallback
    slack-cluster-1: U02XYZ      # exact surface_id override
```

The lookup tries the exact `surface_id` first, then the
`surface_type` fallback.

## 6. Smoke test (no real workspace required)

The unit suite mocks at the WebClient boundary so the adapter is
green without any token:

```bash
pytest metasphere/tests/test_slack_api.py \
       metasphere/tests/test_slack_handler.py \
       metasphere/tests/test_slack_adapter.py \
       metasphere/tests/test_message_cli.py -v
```

Outbound live smoke (no daemon wire-up needed):

```bash
metasphere slack send "hello" --surface slack-relay --channel C012XYZ
```

Inbound live smoke (DM → agent tmux) works once the tokens are
dropped and the gateway is restarted — the daemon auto-discovers
every `~/.metasphere/config/slack*.env` at startup and constructs one
`SlackAdapter` per file (`slack.env` → `slack`, `slack-<name>.env` →
`slack-<name>`). No code change per new bot — just a config file. The
flow:

```bash
# Open a DM to the bot in Slack; the inbound should land in the
# agent's tmux. Then test outbound via the active_conversation pin:
METASPHERE_AGENT_ID=@relay metasphere message send \
    "hello from relay" --surface auto
```

The `--surface auto` path requires an inbound to have landed first
(so the `active_conversation` pin exists). Until then, fall back to
explicit `metasphere message send "..." --surface slack-relay
--chat-id <C-id>` (or the outbound `metasphere slack send` shown
above).

## 7. Operator checklist

Bringing a bot online is entirely config — no code change:

- [ ] Pick the workspace (§0).
- [ ] Create the Slack app(s) (§1).
- [ ] Drop the token file(s) (§2).
- [ ] Seed the agent identities and add `surfaces:` to their specs (§3).
- [ ] Restart the gateway so the daemon discovers the new token
      file(s). Adapter construction runs at startup: drop a token
      file, restart, done.
- [ ] Live smoke test via the gateway daemon; watch `journalctl -u
      metasphere-gateway` for `"slack socket-mode worker for
      slack-<name> crashed"` markers.

## Notes / limitations

- Socket Mode means no public webhook — one long-lived websocket per
  Slack app lives in the gateway daemon. Each runs in its own daemon
  thread; the gateway's main poll-tick drains an in-process queue per
  tick.
- The handler is intentionally narrow: only `message.im` + `app_mention`
  reach the agent. Channel chatter the bot wasn't tagged in NEVER
  surfaces. This is both server-side (event scope subscription) and
  client-side (`channel_type` / event-type guards).
- `send_document` uses `files_upload_v2` (the post-2025 upload API);
  `files.upload` is deprecated and will not be added.
