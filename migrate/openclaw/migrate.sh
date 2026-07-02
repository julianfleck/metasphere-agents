#!/bin/bash
# OpenClaw migration. Imports config, workspace, memory, SOUL.md,
# CAM data, and skills into the metasphere install. Idempotent.
#
# Environment (inherited from install.sh):
#   METASPHERE_DIR   target install root (required)
#   INTERACTIVE      "true" / "false" — controls the optional
#                    "disable OpenClaw gateway" prompt
#   OPENCLAW_DIR     source dir (default ~/.openclaw)
set -e

OPENCLAW_DIR="${OPENCLAW_DIR:-$HOME/.openclaw}"
METASPHERE_DIR="${METASPHERE_DIR:?METASPHERE_DIR must be set}"
INTERACTIVE="${INTERACTIVE:-false}"

# Minimal logging helpers — install.sh's helpers don't cross the
# subshell boundary. Match the same visual style.
if [[ -t 1 ]]; then
    _G='\033[0;32m'; _Y='\033[1;33m'; _C='\033[0;36m'; _N='\033[0m'
else
    _G='' _Y='' _C='' _N=''
fi
ok()   { echo -e "${_G}[ok]${_N} $*"; }
info() { echo -e "${_C}[..]${_N} $*"; }
warn() { echo -e "${_Y}[!!]${_N} $*"; }

info "Migrating OpenClaw configuration from $OPENCLAW_DIR"

# -----------------------------------------------------------------------
# Telegram token
# -----------------------------------------------------------------------
# Canonical schema: .channels.telegram.botToken. Legacy fallbacks
# kept for older openclaw.json shapes.
if [[ -f "$OPENCLAW_DIR/openclaw.json" ]] && command -v jq &>/dev/null; then
    token=$(jq -r '
        .channels.telegram.botToken
        // .telegram.botToken
        // .TELEGRAM_BOT_TOKEN
        // .env.TELEGRAM_BOT_TOKEN
        // empty
    ' "$OPENCLAW_DIR/openclaw.json" 2>/dev/null || echo "")

    if [[ -n "$token" && "$token" != "null" && "$token" =~ ^[0-9]+: ]]; then
        mkdir -p "$METASPHERE_DIR/config"
        echo "TELEGRAM_BOT_TOKEN=$token" > "$METASPHERE_DIR/config/telegram.env"
        chmod 600 "$METASPHERE_DIR/config/telegram.env"
        ok "Migrated Telegram token from OpenClaw"
    elif [[ -n "$token" && "$token" != "null" ]]; then
        warn "openclaw.json token looks invalid (skipping)"
    fi
elif [[ ! -f "$OPENCLAW_DIR/openclaw.json" ]]; then
    info "No openclaw.json — Telegram token will be configured manually"
fi

# -----------------------------------------------------------------------
# Workspace + memory db pointers
# -----------------------------------------------------------------------
mkdir -p "$METASPHERE_DIR/config"
if [[ -d "$OPENCLAW_DIR/workspace" ]]; then
    echo "$OPENCLAW_DIR/workspace" > "$METASPHERE_DIR/config/openclaw_workspace"
    ok "Registered openclaw workspace pointer"
fi
if [[ -f "$OPENCLAW_DIR/memory/main.sqlite" ]]; then
    echo "$OPENCLAW_DIR/memory/main.sqlite" > "$METASPHERE_DIR/config/openclaw_memory_db"
    ok "Registered openclaw memory db pointer"
fi

# -----------------------------------------------------------------------
# SOUL.md (only if metasphere doesn't already have one)
# -----------------------------------------------------------------------
mkdir -p "$METASPHERE_DIR/agents/@orchestrator"
soul_src=""
if [[ -f "$OPENCLAW_DIR/workspace/SOUL.md" ]]; then
    soul_src="$OPENCLAW_DIR/workspace/SOUL.md"
elif [[ -f "$OPENCLAW_DIR/SOUL.md" ]]; then
    soul_src="$OPENCLAW_DIR/SOUL.md"
fi
if [[ -n "$soul_src" && ! -f "$METASPHERE_DIR/agents/@orchestrator/SOUL.md" ]]; then
    cp "$soul_src" "$METASPHERE_DIR/agents/@orchestrator/SOUL.md"
    ok "Seeded SOUL.md from $soul_src"
fi

# -----------------------------------------------------------------------
# CAM data (cross-user openclaw home case)
# -----------------------------------------------------------------------
if [[ ! -d "$HOME/.cam" ]]; then
    openclaw_home=$(dirname "$OPENCLAW_DIR")
    cam_src="$openclaw_home/.cam"
    if [[ -d "$cam_src" ]]; then
        info "Found openclaw CAM data at $cam_src — linking into $HOME/.cam"
        if ln -s "$cam_src" "$HOME/.cam" 2>/dev/null; then
            ok "Linked CAM data — no re-index needed"
        else
            warn "Symlink failed — falling back to copy"
            cp -a "$cam_src" "$HOME/.cam" && ok "Copied CAM data" \
                || warn "Copy failed"
        fi
    fi
fi

# -----------------------------------------------------------------------
# Skills (symlink, non-destructive)
# -----------------------------------------------------------------------
if [[ -d "$OPENCLAW_DIR/skills" ]]; then
    mkdir -p "$METASPHERE_DIR/skills"
    linked=0
    shopt -s nullglob
    for skill in "$OPENCLAW_DIR/skills"/*/; do
        name=$(basename "$skill")
        [[ "$name" == _* ]] && continue
        if [[ ! -e "$METASPHERE_DIR/skills/$name" ]]; then
            ln -s "$skill" "$METASPHERE_DIR/skills/$name" 2>/dev/null && ((linked++)) || true
        fi
    done
    shopt -u nullglob
    [[ $linked -gt 0 ]] && ok "Linked $linked openclaw skills"
fi

# -----------------------------------------------------------------------
# Mark source as migrated
# -----------------------------------------------------------------------
if [[ -f "$OPENCLAW_DIR/openclaw.json" ]] && command -v jq &>/dev/null; then
    tmp=$(mktemp)
    jq '. + {metasphere_migrated: true, migrated_at: now | tostring}' \
        "$OPENCLAW_DIR/openclaw.json" > "$tmp" 2>/dev/null \
        && mv "$tmp" "$OPENCLAW_DIR/openclaw.json" \
        || rm -f "$tmp"
fi

# -----------------------------------------------------------------------
# Optional: offer to disable OpenClaw's own gateway (interactive only)
# -----------------------------------------------------------------------
if [[ "$INTERACTIVE" == "true" ]]; then
    gateway_running=false
    if [[ "$(uname)" == "Darwin" ]]; then
        launchctl list 2>/dev/null | grep -q "openclaw" && gateway_running=true
    else
        systemctl --user is-active openclaw-gateway &>/dev/null && gateway_running=true
    fi

    if $gateway_running; then
        echo
        echo "OpenClaw gateway is currently running."
        read -p "Disable OpenClaw gateway (Metasphere will take over)? [Y/n] " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Nn]$ ]]; then
            info "Disabling OpenClaw gateway..."
            if [[ "$(uname)" == "Darwin" ]]; then
                plist="$HOME/Library/LaunchAgents/com.openclaw.gateway.plist"
                if [[ -f "$plist" ]]; then
                    launchctl unload "$plist" 2>/dev/null || true
                    mv "$plist" "${plist}.disabled"
                    ok "Disabled OpenClaw (launchd)"
                fi
            else
                systemctl --user stop openclaw-gateway 2>/dev/null || true
                systemctl --user disable openclaw-gateway 2>/dev/null || true
                ok "Disabled OpenClaw (systemd)"
            fi
            if [[ -f "$OPENCLAW_DIR/openclaw.json" ]] && command -v jq &>/dev/null; then
                tmp=$(mktemp)
                jq '. + {gateway_disabled: true}' "$OPENCLAW_DIR/openclaw.json" > "$tmp" 2>/dev/null \
                    && mv "$tmp" "$OPENCLAW_DIR/openclaw.json" \
                    || rm -f "$tmp"
            fi
        fi
    fi
fi
