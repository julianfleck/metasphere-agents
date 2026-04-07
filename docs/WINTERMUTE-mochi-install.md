# Wintermute → Mochi: Install Metasphere on the Bean VM

**Audience:** @wintermute (you have ssh + machinectl access on `data.basicbold.de`).
**Goal:** Replace the legacy tinyclaw install inside the `mochi` nspawn container with the metasphere harness from `github.com/<owner>/metasphere-agents@main`, preserving the existing Telegram bot identity and the user-facing memory/agent files.

This is a clean re-install of the harness. The only data you must carry over is the *content* of the user-facing markdown files (memory + agents) and the existing Telegram credentials. Everything else (cron jobs, openclaw bridges, sqlite, etc.) is intentionally dropped.

---

## 0. Pre-flight (host shell on data.basicbold.de)

```bash
# Confirm mochi exists and is running
machinectl list | grep -i mochi
machinectl status mochi

# If not running, start it
sudo machinectl start mochi
```

If `mochi` isn't the right container name, check `machinectl list` for the bean-equivalent. Julian called it "bean (might be called mochi)" — if you see `bean` instead, substitute throughout.

---

## 1. Inventory the legacy tinyclaw install (do not delete yet)

Get a shell inside mochi as the resident user:

```bash
# Drop into the container as its main user
sudo machinectl shell <user>@mochi /bin/bash

# Inside mochi, find the tinyclaw home and inventory
ls -la ~/.tinyclaw/ 2>/dev/null || ls -la ~/.openclaw/ 2>/dev/null || ls -la ~ | grep -iE 'claw|tiny|spot|metasphere'
```

You're looking for the equivalents of these spot/openclaw paths (the file *names* matter, the directory prefix may differ on tinyclaw):

| Path on spot                                         | Why we need it             |
|------------------------------------------------------|----------------------------|
| `~/.openclaw/openclaw.json`                          | Telegram bot token + chat id (CRITICAL — preserve) |
| `~/.openclaw/workspace/MEMORY.md`                    | Long-term curated memory   |
| `~/.openclaw/workspace/SOUL.md`                      | Persona / values           |
| `~/.openclaw/workspace/IDENTITY.md`                  | Name, role, id metadata    |
| `~/.openclaw/workspace/USER.md`                      | Who Julian is              |
| `~/.openclaw/workspace/AGENTS.md`                    | Local agent registry       |
| `~/.openclaw/workspace/TOOLS.md`                     | Local conventions, channel ids, device nicknames |
| `~/.openclaw/workspace/memory/YYYY-MM-DD.md`         | Daily logs (all of them)   |
| `~/.openclaw/memory/main.sqlite`                     | CAM FTS index (optional — metasphere FTS can re-index from the .md files; only carry it over if rebuilding is expensive) |

Tinyclaw will likely have these under `~/.tinyclaw/...` with the same filenames. Whatever subset exists is what you migrate.

Save the inventory output to `~/migration-inventory.txt` so you can verify migration completeness afterward:

```bash
find ~/.tinyclaw ~/.openclaw -type f \( -name '*.md' -o -name '*.json' -o -name '*.sqlite' \) 2>/dev/null > ~/migration-inventory.txt
wc -l ~/migration-inventory.txt
```

---

## 2. Stage the legacy data outside the install path

Create a staging dir that survives the metasphere install. Do **not** remove the originals yet — only after validation in step 6.

```bash
STAGE=~/legacy-tinyclaw-snapshot-$(date +%Y%m%d-%H%M%S)
mkdir -p "$STAGE"

# Telegram credentials — extract just the token and the chat id
# (we won't carry the whole openclaw.json, only the bits metasphere needs)
LEGACY_CONFIG=$(ls ~/.tinyclaw/tinyclaw.json ~/.openclaw/openclaw.json 2>/dev/null | head -1)
if [ -n "$LEGACY_CONFIG" ]; then
  cp -a "$LEGACY_CONFIG" "$STAGE/legacy-config.json"
fi

# All workspace markdown
for src in ~/.tinyclaw/workspace ~/.openclaw/workspace; do
  if [ -d "$src" ]; then
    mkdir -p "$STAGE/workspace"
    cp -a "$src"/*.md "$STAGE/workspace/" 2>/dev/null || true
    if [ -d "$src/memory" ]; then
      mkdir -p "$STAGE/workspace/memory"
      cp -a "$src/memory"/*.md "$STAGE/workspace/memory/" 2>/dev/null || true
    fi
  fi
done

ls -R "$STAGE"
```

Verify the staged directory has at least: `legacy-config.json`, `workspace/MEMORY.md` (or equivalent), and a non-empty `workspace/memory/` if there were daily logs. If any of these are missing on tinyclaw, that's fine — we install with whatever exists.

---

## 3. Install metasphere from the github repo

```bash
# Inside mochi, as the resident user:
mkdir -p ~/Code
cd ~/Code
git clone https://github.com/<owner>/metasphere-agents.git
# If the repo is private, ensure GITHUB_TOKEN is set first or use ssh remote.

cd ~/Code/metasphere-agents

# Run the installer. It will:
#   - create ~/.metasphere/
#   - install the python package via pip into a venv it manages
#   - register systemd --user units for heartbeat / schedule / telegram
#   - write skeleton agent dir for @orchestrator
./install.sh
```

Read `install.sh` first if anything looks off — it touches `~/.metasphere/`, `~/.config/systemd/user/metasphere-*.service`, and `~/.claude/settings.local.json` for the hooks.

After install completes, do **not** start the daemons yet. We need to wire credentials first.

---

## 4. Wire the telegram credentials from the legacy snapshot

The metasphere telegram stack reads these files in order (canonical-first as of commit `f80e0b9`):

1. `TELEGRAM_BOT_TOKEN` env var
2. `~/.metasphere/config/telegram.env` (`TELEGRAM_BOT_TOKEN=...`)
3. `TELEGRAM_BOT_TOKEN_REWRITE` env var
4. `~/.metasphere/config/telegram-rewrite.env`

Plus the chat id from `~/.metasphere/config/telegram.env` (`TELEGRAM_CHAT_ID=...`) or `~/.metasphere/config/telegram_chat_id` (bare value).

Extract from the staged legacy config:

```bash
# Adapt the jq paths to the actual structure of legacy-config.json.
# Tinyclaw and openclaw both store the bot token under channels.telegram.botToken.
LEGACY_CONFIG="$STAGE/legacy-config.json"

BOT_TOKEN=$(jq -r '.channels.telegram.botToken // empty' "$LEGACY_CONFIG")
CHAT_ID=$(jq -r '.channels.telegram.chatId // .channels.telegram.dmPolicy.defaultChatId // empty' "$LEGACY_CONFIG")

# If chatId is null (openclaw stores it elsewhere), check these fallback files:
[ -z "$CHAT_ID" ] && CHAT_ID=$(cat ~/.tinyclaw/telegram_chat_id ~/.openclaw/telegram_chat_id 2>/dev/null | head -1)

# Sanity check before writing
test -n "$BOT_TOKEN" || { echo "BOT_TOKEN is empty — abort"; exit 1; }
test -n "$CHAT_ID"   || { echo "CHAT_ID is empty — Julian must /start the bot once after install to populate it"; }

mkdir -p ~/.metasphere/config
umask 077
cat > ~/.metasphere/config/telegram.env <<EOF
TELEGRAM_BOT_TOKEN=$BOT_TOKEN
TELEGRAM_CHAT_ID=$CHAT_ID
EOF
chmod 600 ~/.metasphere/config/telegram.env
```

Verify by running the canonical token check:

```bash
cd ~/Code/metasphere-agents
~/.metasphere/venv/bin/metasphere-telegram getme
# Expected: a JSON dict with the bot username matching what tinyclaw was using.
```

If `getme` returns the wrong bot, the wrong token was extracted — go back and fix `BOT_TOKEN` before continuing. **Do not** proceed to step 5 with the wrong token; you'll start the daemon polling the wrong bot.

---

## 5. Migrate the user-facing markdown files

The metasphere harness reads persona/identity from these locations on each turn (via `metasphere-context`):

| Target on metasphere                                         | Source from staged snapshot           |
|--------------------------------------------------------------|---------------------------------------|
| `~/.metasphere/agents/@orchestrator/SOUL.md`                 | `$STAGE/workspace/SOUL.md`            |
| `~/.metasphere/agents/@orchestrator/IDENTITY.md`             | `$STAGE/workspace/IDENTITY.md`        |
| `~/.metasphere/agents/@orchestrator/MISSION.md`              | (write a fresh one if none exists; tinyclaw has no MISSION.md equivalent — see below) |
| `~/.metasphere/agents/@orchestrator/MEMORY.md`               | `$STAGE/workspace/MEMORY.md`          |
| `~/.metasphere/agents/@orchestrator/USER.md`                 | `$STAGE/workspace/USER.md`            |
| `~/.metasphere/agents/@orchestrator/AGENTS.md`               | `$STAGE/workspace/AGENTS.md`          |
| `~/.metasphere/agents/@orchestrator/TOOLS.md`                | `$STAGE/workspace/TOOLS.md`           |
| `~/.metasphere/agents/@orchestrator/daily/<YYYY-MM-DD>.md`   | `$STAGE/workspace/memory/<date>.md` (one per file, same name) |

Copy them in:

```bash
DEST=~/.metasphere/agents/@orchestrator
mkdir -p "$DEST/daily"

for f in SOUL IDENTITY MEMORY USER AGENTS TOOLS; do
  if [ -f "$STAGE/workspace/$f.md" ]; then
    cp -a "$STAGE/workspace/$f.md" "$DEST/$f.md"
    echo "  ← $f.md"
  fi
done

if [ -d "$STAGE/workspace/memory" ]; then
  cp -a "$STAGE/workspace/memory"/*.md "$DEST/daily/" 2>/dev/null || true
  echo "  ← daily logs: $(ls "$DEST/daily" | wc -l)"
fi

ls -la "$DEST"
```

If `MISSION.md` does not exist, write a minimal one — the metasphere wake mechanism uses MISSION.md presence as its "this is a persistent agent" detector:

```bash
cat > "$DEST/MISSION.md" <<'EOF'
# Mission: @orchestrator on mochi

You are the resident orchestrator on the mochi VM, freshly migrated from
the legacy tinyclaw harness on 2026-04-07. Your job is the same as it was
under tinyclaw: respond to Julian via Telegram, maintain memory and daily
logs, manage tasks and messages in the fractal scope under
~/Code/metasphere-agents, and evolve the harness when you spot friction.

Persona, voice, and accumulated context are in SOUL.md / IDENTITY.md /
MEMORY.md / daily/. Read those before answering anything substantive.
EOF
```

---

## 6. Start the daemons + smoke-test

```bash
systemctl --user daemon-reload
systemctl --user enable --now metasphere-heartbeat.service \
                                metasphere-schedule.service \
                                metasphere-telegram.service
systemctl --user status metasphere-heartbeat metasphere-schedule metasphere-telegram --no-pager | head -40
```

Smoke test from inside mochi:

```bash
cd ~/Code/metasphere-agents

# 1. The token is right
~/.metasphere/venv/bin/metasphere-telegram getme

# 2. The chat id is right and a real send works
METASPHERE_AGENT_ID=@orchestrator \
  ~/.metasphere/venv/bin/metasphere-telegram send \
  "mochi: metasphere harness installed and online. Reusing the legacy tinyclaw bot. Memory + identity migrated. Standing by."

# 3. Heartbeat fires and the daemon log is healthy
tail -20 ~/.metasphere/logs/heartbeat.log

# 4. Tests pass against the real tree (excludes live tests by default)
~/.metasphere/venv/bin/python -m pytest ~/Code/metasphere-agents/metasphere/tests/ -q
```

If the send in step 2 lands in Julian's chat, the migration is functionally complete.

---

## 7. Decommission the legacy tinyclaw install

**Only after Julian confirms** the metasphere install works on mochi, disable + stop everything tinyclaw:

```bash
# Inside mochi:
systemctl --user list-units --all | grep -iE 'tiny|claw|bean'
# For each tinyclaw unit:
systemctl --user stop  <unit>
systemctl --user disable <unit>
```

If tinyclaw was registered in `~/.config/systemd/user/`, also delete (or move aside) the unit files so a future `daemon-reload` doesn't accidentally re-pull them.

Do **not** delete `~/.tinyclaw/` itself yet — leave it on disk for at least a week as a recovery snapshot. The staging dir from step 2 (`$STAGE`) is also kept until Julian explicitly says it's safe to drop.

Add an entry to mochi's daily log noting the migration date, the legacy snapshot path, and the metasphere commit hash that was installed.

---

## 8. Report back

Once the smoke test in step 6 passes, message the spot @orchestrator from mochi's @orchestrator with:

```bash
metasphere-telegram send "@spot mochi migration complete. Metasphere main commit <hash>. Token reused from tinyclaw. Smoke send delivered. Legacy snapshot at <STAGE_PATH>. Tests N passed, M deselected."
```

(Or just message Julian directly if cross-host orchestrator messaging isn't wired yet — the telegram channel works either way.)

Then drop a one-line summary into `~/.metasphere/agents/@orchestrator/daily/2026-04-07.md` on mochi so the next session has the migration story.

---

## Things to NOT do

- **Do not** copy the openclaw or tinyclaw systemd units across — they fight metasphere on `getUpdates` and emit legacy cron spam (see today's spot postmortem). Only the metasphere units should be active.
- **Do not** copy the openclaw cron `jobs.json` directly — it's openclaw-specific. If mochi has crons worth keeping, port them to metasphere's schedule format separately.
- **Do not** enable `pytest -m live` on mochi — the live tests send 6KB of `yyyy` to Julian's chat and were the source of today's all-day spam. The default `pytest` invocation on metasphere main excludes them via `addopts`.
- **Do not** skip the `getme` verification in step 4. Installing a daemon polling the wrong bot is the worst-case outcome and silently spams or steals updates from another bot.

---

## If you get stuck

- Missing token in `legacy-config.json`: ask Julian for the bot token directly. He has it.
- `getme` returns 401: the token was rotated or copied wrong. Re-extract.
- Daemons start but `metasphere-telegram poll` logs `Conflict: terminated by other getUpdates`: another process on this host or another host is polling the same token. Find and kill it before continuing.
- Anything destructive (rm, systemctl disable a non-tinyclaw unit, force-pushing): stop and ask Julian first.
