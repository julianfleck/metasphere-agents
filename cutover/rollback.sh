#!/usr/bin/env bash
# cutover/rollback.sh — reverse cutover/apply.sh using the most recent
# bin.backup-cutover-* directory (or pass a specific one as $1).
set -euo pipefail

BIN_DIR="$HOME/.metasphere/bin"
SYSTEMD_DIR="$HOME/.config/systemd/user"
REPO_SETTINGS="/home/openclaw/Code/metasphere-agents/.claude/settings.local.json"

if [ $# -ge 1 ]; then
  BACKUP_DIR="$1"
else
  BACKUP_DIR="$(ls -1d "$HOME"/.metasphere/bin.backup-cutover-* 2>/dev/null | sort | tail -n1 || true)"
fi

if [ -z "${BACKUP_DIR:-}" ] || [ ! -d "$BACKUP_DIR" ]; then
  echo "[rollback] no backup directory found" >&2
  exit 1
fi
echo "[rollback] using $BACKUP_DIR"

# Restore bin shims (only files at the top level — systemd/ and settings live alongside).
for f in "$BACKUP_DIR"/*; do
  [ -f "$f" ] || continue
  name="$(basename "$f")"
  case "$name" in
    settings.local.json) continue ;;
  esac
  cp -a "$f" "$BIN_DIR/$name"
  echo "[rollback] restored $name"
done

# Restore systemd units.
if [ -d "$BACKUP_DIR/systemd" ]; then
  for u in "$BACKUP_DIR/systemd"/*.service; do
    [ -f "$u" ] || continue
    cp -a "$u" "$SYSTEMD_DIR/$(basename "$u")"
    echo "[rollback] restored $(basename "$u")"
  done
  systemctl --user daemon-reload
  systemctl --user restart metasphere-heartbeat.service metasphere-telegram.service metasphere-schedule.service
fi

# Restore repo settings.
if [ -f "$BACKUP_DIR/settings.local.json" ]; then
  cp -a "$BACKUP_DIR/settings.local.json" "$REPO_SETTINGS"
  echo "[rollback] restored settings.local.json"
fi

echo "[rollback] ROLLBACK COMPLETE"
