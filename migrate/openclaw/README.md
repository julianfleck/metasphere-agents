# OpenClaw migration

Imports state from the OpenClaw precursor harness into a metasphere
install.

## What it imports

- **Telegram bot token** from
  `~/.openclaw/openclaw.json:.channels.telegram.botToken`
  (falls back to legacy schema keys) → `$METASPHERE_DIR/config/telegram.env`.
- **Workspace pointer** if `~/.openclaw/workspace/` exists →
  `$METASPHERE_DIR/config/openclaw_workspace`.
- **Memory database pointer** if `~/.openclaw/memory/main.sqlite`
  exists → `$METASPHERE_DIR/config/openclaw_memory_db`.
- **SOUL.md** from `~/.openclaw/workspace/SOUL.md` (or
  `~/.openclaw/SOUL.md`) → `$METASPHERE_DIR/agents/@orchestrator/SOUL.md`,
  only if the destination doesn't already exist.
- **CAM data** at `~/.openclaw/../.cam` (cross-user case) symlinked
  into `$HOME/.cam` if not already present.
- **Skills** from `~/.openclaw/skills/*/` symlinked into
  `$METASPHERE_DIR/skills/`.
- **Mark migrated**: stamps `metasphere_migrated: true` +
  `migrated_at` into the source `openclaw.json`.

If interactive and the OpenClaw gateway (launchd / systemd) is
running, also offers to disable it.

## Detection

`detect.sh` exits 0 when `~/.openclaw/` (or `$OPENCLAW_DIR` if set)
exists as a directory.

## Idempotence

Re-running is safe — every step checks before writing. The Telegram
token, workspace pointer, and memory pointer get re-written; the
SOUL.md seed, CAM symlink, and skill symlinks no-op when the target
already exists.

## Skipping

`install.sh --no-migrate-openclaw` skips this migration even when
`~/.openclaw/` is present.
