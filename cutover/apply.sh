#!/usr/bin/env bash
# cutover/apply.sh — flip ~/.metasphere/bin shims and systemd units to the
# python entry points in metasphere.cli.*. Reversible via cutover/rollback.sh.
set -euo pipefail

DATE="$(date +%Y%m%d-%H%M%S)"
BIN_DIR="$HOME/.metasphere/bin"
BACKUP_DIR="$HOME/.metasphere/bin.backup-cutover-$DATE"
SYSTEMD_DIR="$HOME/.config/systemd/user"
REPO_SETTINGS="/home/openclaw/Code/metasphere-agents/.claude/settings.local.json"

# name → python module under metasphere.cli
declare -A MAP=(
  [messages]=messages
  [tasks]=tasks
  [metasphere]=metasphere
  [metasphere-schedule]=schedule
  [metasphere-context]=context
  [metasphere-events]=events
  [metasphere-telegram]=telegram
  [metasphere-telegram-stream]=telegram_stream
  [metasphere-heartbeat]=heartbeat
  [metasphere-posthook]=posthook
  [metasphere-trace]=trace
  [metasphere-session]=session
  [metasphere-project]=project
  [metasphere-spawn]=spawn
  [metasphere-wake]=wake
  [metasphere-fts]=fts
  [metasphere-telegram-groups]=telegram_groups
  [metasphere-git-hooks]=git_hooks
  [metasphere-agent]=agent
)

mkdir -p "$BACKUP_DIR"
echo "[apply] backup dir: $BACKUP_DIR"

for name in "${!MAP[@]}"; do
  module="${MAP[$name]}"
  target="$BIN_DIR/$name"
  if [ -e "$target" ]; then
    cp -a "$target" "$BACKUP_DIR/$name"
  fi
  cat > "$target" <<EOF
#!/usr/bin/env bash
# python-harness shim — installed by cutover/apply.sh on $DATE
exec python -m metasphere.cli.$module "\$@"
EOF
  chmod +x "$target"
  echo "[apply] shim: $name → metasphere.cli.$module"
done

# Record which units we touched + their original ExecStart for rollback.
mkdir -p "$BACKUP_DIR/systemd"
for unit in metasphere-heartbeat metasphere-telegram metasphere-schedule; do
  if [ -f "$SYSTEMD_DIR/$unit.service" ]; then
    cp -a "$SYSTEMD_DIR/$unit.service" "$BACKUP_DIR/systemd/$unit.service"
  fi
done

python_bin="$(command -v python)"
sed -i -E "s|^ExecStart=.*|ExecStart=$python_bin -m metasphere.cli.heartbeat daemon 300|" \
  "$SYSTEMD_DIR/metasphere-heartbeat.service"
sed -i -E "s|^ExecStart=.*|ExecStart=$python_bin -m metasphere.cli.telegram poll|" \
  "$SYSTEMD_DIR/metasphere-telegram.service"
sed -i -E "s|^ExecStart=.*|ExecStart=$python_bin -m metasphere.cli.schedule daemon|" \
  "$SYSTEMD_DIR/metasphere-schedule.service"

systemctl --user daemon-reload
systemctl --user restart metasphere-heartbeat.service metasphere-telegram.service metasphere-schedule.service

# Repo .claude/settings.local.json hook flip.
if [ -f "$REPO_SETTINGS" ]; then
  cp -a "$REPO_SETTINGS" "$BACKUP_DIR/settings.local.json"
  python - <<PY
import json, pathlib
p = pathlib.Path("$REPO_SETTINGS")
data = json.loads(p.read_text())
hooks = data.setdefault("hooks", {})
def set_hook(event, cmd):
    hooks[event] = [{"hooks": [{"type": "command", "command": cmd}]}]
set_hook("Stop", "python -m metasphere.cli.posthook")
set_hook("UserPromptSubmit", "python -m metasphere.cli.context")
p.write_text(json.dumps(data, indent=2) + "\n")
PY
fi

echo "[apply] backup at $BACKUP_DIR — CUTOVER COMPLETE"
