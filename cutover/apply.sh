#!/usr/bin/env bash
# cutover/apply.sh — flip ~/.metasphere/bin shims and systemd units to the
# python console-script entry points installed by `pip install -e .` in this
# repo. Reversible via cutover/rollback.sh.
#
# The shim files in $HOME/.metasphere/bin point at the absolute path of the
# console-script binary in the active Python environment. We resolve that
# path once via sysconfig so the shims do not infinite-loop through PATH
# (which has $HOME/.metasphere/bin first).
set -euo pipefail

DATE="$(date +%Y%m%d-%H%M%S)"
BIN_DIR="$HOME/.metasphere/bin"
BACKUP_DIR="$HOME/.metasphere/bin.backup-cutover-$DATE"
SYSTEMD_DIR="$HOME/.config/systemd/user"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_SETTINGS="$REPO_ROOT/.claude/settings.local.json"

SCRIPTS_DIR="$(python -c 'import sysconfig; print(sysconfig.get_path("scripts"))')"
if [ -z "$SCRIPTS_DIR" ] || [ ! -d "$SCRIPTS_DIR" ]; then
  echo "[apply] cannot resolve python scripts dir ($SCRIPTS_DIR)" >&2
  exit 1
fi
echo "[apply] python scripts dir: $SCRIPTS_DIR"

# Console-script binary names installed by pyproject.toml [project.scripts].
BINARIES=(
  messages
  tasks
  metasphere-context
  metasphere-posthook
  metasphere-heartbeat
  metasphere-schedule
  metasphere-telegram
  metasphere-trace
  metasphere-session
  metasphere-project
  metasphere-telegram-groups
  metasphere-git-hooks
  metasphere-gateway
  metasphere-fts
  metasphere-agent
  metasphere-spawn
  metasphere-wake
)

# Verify every binary exists before touching anything.
for name in "${BINARIES[@]}"; do
  if [ ! -x "$SCRIPTS_DIR/$name" ]; then
    echo "[apply] FATAL: missing console script $SCRIPTS_DIR/$name" >&2
    echo "[apply] run: pip install -e $REPO_ROOT" >&2
    exit 1
  fi
done

mkdir -p "$BACKUP_DIR" "$BIN_DIR"
echo "[apply] backup dir: $BACKUP_DIR"

for name in "${BINARIES[@]}"; do
  target="$BIN_DIR/$name"
  # IMPORTANT: pre-cutover, $BIN_DIR/$name is often a symlink into the repo
  # (scripts/<name>). We must back up the link AS A LINK and then unlink it,
  # otherwise `cat > "$target"` would follow the symlink and overwrite the
  # tracked file in the repo working tree.
  if [ -L "$target" ]; then
    cp -P "$target" "$BACKUP_DIR/$name"
    rm "$target"
  elif [ -e "$target" ]; then
    cp -a "$target" "$BACKUP_DIR/$name"
    rm -f "$target"
  fi
  cat > "$target" <<EOF
#!/usr/bin/env bash
# python-harness shim — installed by cutover/apply.sh on $DATE
exec "$SCRIPTS_DIR/$name" "\$@"
EOF
  chmod +x "$target"
  echo "[apply] shim: $name → $SCRIPTS_DIR/$name"
done

# Rewrite systemd unit ExecStart lines to the absolute binary paths.
mkdir -p "$BACKUP_DIR/systemd"
declare -A UNIT_CMD=(
  [metasphere-heartbeat]="$SCRIPTS_DIR/metasphere-heartbeat daemon 300"
  [metasphere-telegram]="$SCRIPTS_DIR/metasphere-telegram poll"
  [metasphere-schedule]="$SCRIPTS_DIR/metasphere-schedule daemon"
)

for unit in "${!UNIT_CMD[@]}"; do
  unit_file="$SYSTEMD_DIR/$unit.service"
  if [ -f "$unit_file" ]; then
    cp -a "$unit_file" "$BACKUP_DIR/systemd/$unit.service"
    sed -i -E "s|^ExecStart=.*|ExecStart=${UNIT_CMD[$unit]}|" "$unit_file"
    echo "[apply] systemd: $unit → ${UNIT_CMD[$unit]}"
  else
    echo "[apply] note: $unit_file not present, skipping" >&2
  fi
done

systemctl --user daemon-reload
systemctl --user restart metasphere-heartbeat.service metasphere-telegram.service metasphere-schedule.service

# Disable legacy openclaw units. Their config was migrated into ~/.metasphere/
# at install time, but the units themselves keep running and race the metasphere
# daemons (notably openclaw-gateway shares TELEGRAM_BOT_TOKEN, which causes a
# getUpdates conflict and a stream of legacy-cron spam to the user's chat).
# Discovered 2026-04-07 — see daily log.
for legacy in openclaw-gateway.service rage-bridge.service rage-bridge.timer telegram-watchdog.service; do
  if systemctl --user list-unit-files "$legacy" >/dev/null 2>&1; then
    systemctl --user stop "$legacy" 2>/dev/null || true
    systemctl --user disable "$legacy" 2>/dev/null || true
    echo "[apply] legacy: stopped + disabled $legacy"
  fi
done

# Repo .claude/settings.local.json hook flip — point at console-script names.
if [ -f "$REPO_SETTINGS" ]; then
  cp -a "$REPO_SETTINGS" "$BACKUP_DIR/settings.local.json"
  python - <<PY
import json, pathlib
p = pathlib.Path("$REPO_SETTINGS")
data = json.loads(p.read_text())
hooks = data.setdefault("hooks", {})
def set_hook(event, cmd):
    hooks[event] = [{"hooks": [{"type": "command", "command": cmd}]}]
set_hook("Stop", "metasphere-posthook")
set_hook("UserPromptSubmit", "metasphere-context")
p.write_text(json.dumps(data, indent=2) + "\n")
PY
fi

echo "[apply] backup at $BACKUP_DIR — CUTOVER COMPLETE"
