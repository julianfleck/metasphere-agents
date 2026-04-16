#!/bin/bash
#
# Metasphere Agents - Multi-agent orchestration for Claude Code
# One-line installer: curl -fsSL https://raw.githubusercontent.com/julianfleck/metasphere-agents/main/install.sh | bash
#
# Options:
#   -y    Non-interactive mode (use defaults/env vars)
#   -v    Verbose output
#
# Environment variables (for non-interactive):
#   TELEGRAM_BOT_TOKEN    - Telegram bot token
#   METASPHERE_DIR        - Installation directory (default: ~/.metasphere)
#
set -e

REPO="julianfleck/metasphere-agents"
METASPHERE_DIR="${METASPHERE_DIR:-$HOME/.metasphere}"
SCRIPT_DIR="$(cd "$(dirname "$0")" 2>/dev/null && pwd || echo ".")"
INTERACTIVE=true
VERBOSE=false

# Parse arguments
while getopts "yv" opt; do
    case $opt in
        y) INTERACTIVE=false ;;
        v) VERBOSE=true ;;
        *) ;;
    esac
done

# Detect if stdin is terminal
[[ ! -t 0 ]] && INTERACTIVE=false

# Colors
if [[ -t 1 ]]; then
    GREEN='\033[0;32m'
    YELLOW='\033[1;33m'
    RED='\033[0;31m'
    CYAN='\033[0;36m'
    DIM='\033[2m'
    NC='\033[0m'
else
    GREEN='' YELLOW='' RED='' CYAN='' DIM='' NC=''
fi

ok() { echo -e "${GREEN}[ok]${NC} $*"; }
info() { echo -e "${CYAN}[..]${NC} $*"; }
warn() { echo -e "${YELLOW}[!!]${NC} $*"; }
err() { echo -e "${RED}[error]${NC} $*"; exit 1; }

echo "Metasphere Agents"
echo "================="
echo "Multi-agent orchestration for Claude Code"
echo

# =============================================================================
# Dependency checks
# =============================================================================

check_dependencies() {
    info "Checking dependencies..."

    # Git
    if command -v git &>/dev/null; then
        ok "git"
    else
        err "git required - install from https://git-scm.com"
    fi

    # jq
    if command -v jq &>/dev/null; then
        ok "jq"
    else
        warn "jq not found - installing..."
        if [[ "$(uname)" == "Darwin" ]]; then
            brew install jq || err "Failed to install jq"
        elif command -v apt-get &>/dev/null; then
            sudo apt-get install -y jq || err "Failed to install jq"
        else
            err "Please install jq manually"
        fi
        ok "jq installed"
    fi

    # curl
    if command -v curl &>/dev/null; then
        ok "curl"
    else
        err "curl required"
    fi

    # Claude Code CLI
    if command -v claude &>/dev/null; then
        local claude_version=$(claude --version 2>/dev/null | head -1 || echo "unknown")
        ok "claude CLI ($claude_version)"

        # Test if Claude is authenticated with a quick probe
        info "Testing Claude Code authentication..."
        local test_response=$(echo "reply with just 'ok'" | timeout 30 claude -p 2>&1 || echo "FAILED")
        if [[ "$test_response" == *"ok"* ]] || [[ "$test_response" == *"Ok"* ]] || [[ "$test_response" == *"OK"* ]]; then
            ok "Claude Code authenticated"
        elif [[ "$test_response" == *"FAILED"* ]] || [[ "$test_response" == *"error"* ]] || [[ "$test_response" == *"login"* ]]; then
            warn "Claude Code may not be authenticated"
            echo "    Run: claude /login"
            echo "    Then re-run this installer"
            if $INTERACTIVE; then
                read -p "Continue anyway? [y/N] " -n 1 -r
                echo
                [[ ! $REPLY =~ ^[Yy]$ ]] && exit 1
            fi
        else
            ok "Claude Code responding"
        fi
    else
        warn "claude CLI not found"
        echo "    Install from: https://claude.ai/code"
        echo "    Metasphere requires Claude Code for agent execution"
        if $INTERACTIVE; then
            read -p "Continue without Claude? [y/N] " -n 1 -r
            echo
            [[ ! $REPLY =~ ^[Yy]$ ]] && exit 1
        fi
    fi

    # CAM (Collective Agent Memory)
    if command -v cam &>/dev/null; then
        local cam_version=$(cam --version 2>/dev/null || echo "unknown")
        ok "CAM ($cam_version)"
    else
        warn "CAM not found - installing..."
        if command -v uv &>/dev/null; then
            uv tool install git+https://github.com/julianfleck/collective-agent-memory.git 2>&1 | tail -3
        elif command -v pipx &>/dev/null; then
            pipx install git+https://github.com/julianfleck/collective-agent-memory.git 2>&1 | tail -3
        elif command -v pip3 &>/dev/null; then
            pip3 install --user git+https://github.com/julianfleck/collective-agent-memory.git 2>&1 | tail -3
        else
            err "pip/pipx/uv required to install CAM"
        fi
        ok "CAM installed"
    fi

    # systemd (user instance) — required for the gateway/heartbeat/schedule
    # daemons to auto-start and survive reboots. Minimal containers (bare
    # Docker images without --init, WSL-without-systemd, etc.) ship
    # without a user systemd session; metasphere would install the unit
    # files but nothing would ever start them.
    if [[ -n "${METASPHERE_SKIP_SYSTEMD:-}" ]]; then
        warn "systemd check skipped (METASPHERE_SKIP_SYSTEMD set)"
        echo "    You MUST launch metasphere-gateway/heartbeat/schedule manually."
    elif ! command -v systemctl &>/dev/null; then
        err_no_exit "systemctl not found — metasphere needs systemd for its daemons"
        echo "    Common causes: running inside a minimal Docker image, Alpine Linux,"
        echo "    WSL without systemd support, or a distro without systemd."
        echo
        echo "    Options:"
        echo "      1. Re-run in an environment with systemd (most Linux VMs / hosts)."
        echo "      2. Run with METASPHERE_SKIP_SYSTEMD=1 to install files anyway;"
        echo "         you'll have to launch the daemons manually and they won't"
        echo "         survive container restarts."
        exit 1
    elif ! systemctl --user list-units &>/dev/null; then
        err_no_exit "systemctl --user is not responsive"
        echo "    systemd is installed but the user-level instance isn't running."
        echo "    Typical cause: running inside a container where PID 1 isn't"
        echo "    systemd, or a user session without linger enabled."
        echo
        echo "    Options:"
        echo "      1. Enable user lingering (on a host with systemd):"
        echo "         sudo loginctl enable-linger \$(whoami)"
        echo "         Then log out and back in, and re-run this installer."
        echo "      2. Re-run on a host where systemd is PID 1 (most Linux VMs)."
        echo "      3. Run with METASPHERE_SKIP_SYSTEMD=1 to install files anyway;"
        echo "         daemons will not auto-start."
        exit 1
    else
        ok "systemd (user instance available)"
    fi

    echo
}

# Like err() but doesn't exit — caller prints more context and exits itself.
err_no_exit() {
    echo -e "${RED}✗${NC} $1"
}

# =============================================================================
# Directory setup
# =============================================================================

setup_directories() {
    info "Setting up directories..."

    mkdir -p "$METASPHERE_DIR"/{config,agents,telegram/stream,logs}
    mkdir -p "$METASPHERE_DIR/agents/@orchestrator"

    # Set permissions
    chmod 700 "$METASPHERE_DIR/config"

    # Seed default auto-update.env on FRESH installs only — preserves any
    # operator-tuned setting on re-runs. Default: enabled, daily at 4am.
    if [[ ! -f "$METASPHERE_DIR/config/auto-update.env" ]]; then
        cat > "$METASPHERE_DIR/config/auto-update.env" <<'EOF'
# metasphere auto-update configuration
# Managed by `metasphere update --enable|--disable`.
AUTO_UPDATE_ENABLED=true
AUTO_UPDATE_INTERVAL=daily
AUTO_UPDATE_BRANCH=main
AUTO_UPDATE_RESTART_DAEMONS=true
AUTO_UPDATE_NOTIFY=true
EOF
        chmod 600 "$METASPHERE_DIR/config/auto-update.env"
        ok "Seeded default auto-update.env (daily, enabled)"
    fi

    ok "Created $METASPHERE_DIR"
}

# =============================================================================
# Install scripts
# =============================================================================

install_scripts() {
    info "Installing scripts..."

    local BIN_DIR="$METASPHERE_DIR/bin"
    local VENV_DIR="$METASPHERE_DIR/venv"
    mkdir -p "$BIN_DIR"

    # Create / reuse a dedicated venv under $METASPHERE_DIR/venv.
    # Avoids PEP 668 errors on Debian 12+ / Python 3.12+ hosts and
    # keeps metasphere isolated from the system Python. The venv
    # location is stable across reinstalls, so the pip install is
    # editable against the source tree and subsequent git-pulls
    # (metasphere update) pick up changes without re-venv-ing.
    if [[ ! -x "$VENV_DIR/bin/python" ]]; then
        info "Creating metasphere venv at $VENV_DIR..."
        if ! python3 -m venv "$VENV_DIR" 2>/dev/null; then
            err "Failed to create venv. On Debian/Ubuntu: apt install python3-venv"
        fi
        ok "Created venv"
    else
        ok "Reusing existing venv at $VENV_DIR"
    fi

    # Install the unified Python CLI entry point INTO the venv.
    # The single `metasphere` binary dispatches all subcommands via
    # metasphere.cli.main. Individual metasphere-* scripts are no
    # longer symlinked into BIN_DIR (legacy bash kept in scripts/ for
    # reference). Thin shims for `messages` and `tasks` likewise route
    # through `metasphere msg` / `metasphere task`.
    #
    # --no-warn-script-location: pip whines that the venv's bin isn't
    # on PATH globally, but we symlink the single `metasphere` entry
    # into BIN_DIR below, which IS on PATH (via setup_path). Silence
    # the noise.
    if [[ -d "$SCRIPT_DIR" ]]; then
        "$VENV_DIR/bin/pip" install -e "$SCRIPT_DIR" -q \
            --no-warn-script-location 2>&1 | tail -3 || true
    fi

    # Ensure the unified binary exists in BIN_DIR. Prefer the venv
    # entry point; fall back to legacy locations for already-set-up
    # hosts before we gain the venv.
    local pip_bin=""
    for candidate in \
        "$VENV_DIR/bin/metasphere" \
        "${VIRTUAL_ENV:-/nonexistent}/bin/metasphere" \
        "$HOME/.local/bin/metasphere"; do
        if [[ -x "$candidate" ]]; then
            pip_bin="$candidate"
            break
        fi
    done
    if [[ -n "$pip_bin" ]]; then
        ln -sfn "$pip_bin" "$BIN_DIR/metasphere"
    else
        cat > "$BIN_DIR/metasphere" << 'SHIM'
#!/bin/bash
exec python3 -m metasphere.cli.main "$@"
SHIM
        chmod +x "$BIN_DIR/metasphere"
    fi
    ok "Installed unified metasphere CLI → $BIN_DIR/metasphere"

    # Clean up legacy standalone shims (messages, tasks) — everything
    # routes through `metasphere msg` / `metasphere task` now.
    rm -f "$BIN_DIR/messages" "$BIN_DIR/tasks"

    # Remove stale individual metasphere-* symlinks from previous installs.
    for f in "$BIN_DIR"/metasphere-*; do
        local name
        name=$(basename "$f")
        case "$name" in
            metasphere-fts) ;; # keep standalone FTS tool
            *) rm -f "$f" ;;
        esac
    done
    # Remove stale .bak files.
    rm -f "$BIN_DIR"/*.bak "$BIN_DIR"/README.md 2>/dev/null || true

    # Configure PATH in shell profile
    setup_path "$BIN_DIR"
}

setup_path() {
    local BIN_DIR="$1"

    # Already in PATH?
    if [[ ":$PATH:" == *":$BIN_DIR:"* ]]; then
        ok "PATH already configured"
        return
    fi

    # Detect shell and profile
    local shell_name=$(basename "$SHELL")
    local profile=""

    case "$shell_name" in
        zsh)
            profile="$HOME/.zshrc"
            ;;
        bash)
            if [[ -f "$HOME/.bash_profile" ]]; then
                profile="$HOME/.bash_profile"
            else
                profile="$HOME/.bashrc"
            fi
            ;;
        fish)
            profile="$HOME/.config/fish/config.fish"
            ;;
        *)
            profile="$HOME/.profile"
            ;;
    esac

    # Check if already added to profile
    if [[ -f "$profile" ]] && grep -q "metasphere/bin" "$profile" 2>/dev/null; then
        ok "PATH entry exists in $profile"
        return
    fi

    # Add to profile
    local path_line=""
    if [[ "$shell_name" == "fish" ]]; then
        path_line="set -gx PATH $BIN_DIR \$PATH"
    else
        path_line="export PATH=\"$BIN_DIR:\$PATH\""
    fi

    if $INTERACTIVE; then
        read -p "Add metasphere to PATH in $profile? [Y/n] " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Nn]$ ]]; then
            echo "" >> "$profile"
            echo "# Metasphere Agents" >> "$profile"
            echo "$path_line" >> "$profile"
            ok "Added to $profile"
            echo "    Run: source $profile (or restart shell)"
        else
            warn "Skipped PATH setup"
            echo "    Add manually: $path_line"
        fi
    else
        # Non-interactive: add automatically
        echo "" >> "$profile"
        echo "# Metasphere Agents" >> "$profile"
        echo "$path_line" >> "$profile"
        ok "Added to $profile"
    fi

    # Also export for current session
    export PATH="$BIN_DIR:$PATH"
}

# =============================================================================
# OpenClaw Migration
# =============================================================================

OPENCLAW_DIR="${OPENCLAW_DIR:-$HOME/.openclaw}"
OPENCLAW_DETECTED=false
OPENCLAW_HAS_TELEGRAM=false

detect_openclaw() {
    if [[ -d "$OPENCLAW_DIR" ]]; then
        OPENCLAW_DETECTED=true

        # Check for Telegram token (canonical: channels.telegram.botToken)
        if [[ -f "$OPENCLAW_DIR/openclaw.json" ]]; then
            if jq -e '.channels.telegram.botToken // .telegram.botToken // .TELEGRAM_BOT_TOKEN // .env.TELEGRAM_BOT_TOKEN' "$OPENCLAW_DIR/openclaw.json" &>/dev/null 2>&1; then
                OPENCLAW_HAS_TELEGRAM=true
            fi
        fi
    fi
}

migrate_openclaw() {
    if ! $OPENCLAW_DETECTED; then
        return 0
    fi

    echo
    echo "OpenClaw Detected"
    echo "-----------------"
    echo "Found existing OpenClaw installation at $OPENCLAW_DIR"
    echo

    # Show what we found before asking
    echo "  Detected:"
    [[ -d "$OPENCLAW_DIR/workspace" ]] && echo "    - Workspace directory" || echo "    - No workspace directory"
    if [[ -f "$OPENCLAW_DIR/openclaw.json" ]]; then
        echo "    - openclaw.json config file"
        if $OPENCLAW_HAS_TELEGRAM; then
            local preview_token
            preview_token=$(jq -r '.channels.telegram.botToken // .telegram.botToken // .TELEGRAM_BOT_TOKEN // .env.TELEGRAM_BOT_TOKEN // empty' "$OPENCLAW_DIR/openclaw.json" 2>/dev/null)
            if [[ -n "$preview_token" && "$preview_token" != "null" ]]; then
                echo "    - Telegram token: ${preview_token:0:10}...${preview_token: -4}"
            fi
        fi
    else
        echo "    - No openclaw.json (token will need to be entered manually)"
    fi
    [[ -f "$OPENCLAW_DIR/memory/main.sqlite" ]] && echo "    - Memory database"
    echo

    if $INTERACTIVE; then
        read -p "Migrate configuration from OpenClaw? [Y/n] " -n 1 -r
        echo
        if [[ $REPLY =~ ^[Nn]$ ]]; then
            info "Skipped OpenClaw migration"
            return 0
        fi
    fi

    # Inline migration only — don't shell out to metasphere-migrate which
    # may have different guards. Keep everything in one place.
    migrate_openclaw_inline

    # Ask about disabling OpenClaw
    if $INTERACTIVE; then
        echo
        local gateway_running=false
        if [[ "$(uname)" == "Darwin" ]]; then
            launchctl list 2>/dev/null | grep -q "openclaw" && gateway_running=true
        else
            systemctl --user is-active openclaw-gateway &>/dev/null && gateway_running=true
        fi

        if $gateway_running; then
            echo "OpenClaw gateway is currently running."
            read -p "Disable OpenClaw gateway (Metasphere will take over)? [Y/n] " -n 1 -r
            echo
            if [[ ! $REPLY =~ ^[Nn]$ ]]; then
                disable_openclaw_gateway
            fi
        fi
    fi
}

migrate_openclaw_inline() {
    # Inline migration for when script isn't installed yet
    info "Migrating OpenClaw configuration..."

    # Extract Telegram token (canonical openclaw schema: channels.telegram.botToken)
    # Guard: only attempt if openclaw.json exists AND contains a token
    if [[ -f "$OPENCLAW_DIR/openclaw.json" ]] && $OPENCLAW_HAS_TELEGRAM; then
        local token=""
        token=$(jq -r '.channels.telegram.botToken // .telegram.botToken // .TELEGRAM_BOT_TOKEN // .env.TELEGRAM_BOT_TOKEN // empty' "$OPENCLAW_DIR/openclaw.json" 2>/dev/null || echo "")

        # Validate: token must look like a Telegram bot token (digits:alphanum)
        if [[ -n "$token" && "$token" != "null" && "$token" =~ ^[0-9]+: ]]; then
            mkdir -p "$METASPHERE_DIR/config"
            echo "TELEGRAM_BOT_TOKEN=$token" > "$METASPHERE_DIR/config/telegram.env"
            chmod 600 "$METASPHERE_DIR/config/telegram.env"
            ok "Migrated Telegram token from OpenClaw"
            TELEGRAM_BOT_TOKEN="$token"  # Set for later verification
        else
            warn "Found openclaw.json but token looks invalid (skipping)"
        fi
    elif [[ ! -f "$OPENCLAW_DIR/openclaw.json" ]]; then
        info "No openclaw.json found — Telegram token will be configured manually"
    fi

    # Register openclaw workspace as live legacy context source
    mkdir -p "$METASPHERE_DIR/config"
    if [[ -d "$OPENCLAW_DIR/workspace" ]]; then
        echo "$OPENCLAW_DIR/workspace" > "$METASPHERE_DIR/config/openclaw_workspace"
        ok "Registered openclaw workspace for live context injection"
    fi
    if [[ -f "$OPENCLAW_DIR/memory/main.sqlite" ]]; then
        echo "$OPENCLAW_DIR/memory/main.sqlite" > "$METASPHERE_DIR/config/openclaw_memory_db"
        ok "Registered openclaw memory db"
    fi

    # Seed @orchestrator SOUL.md from workspace if absent
    mkdir -p "$METASPHERE_DIR/agents/@orchestrator"
    local soul_src=""
    if [[ -f "$OPENCLAW_DIR/workspace/SOUL.md" ]]; then
        soul_src="$OPENCLAW_DIR/workspace/SOUL.md"
    elif [[ -f "$OPENCLAW_DIR/SOUL.md" ]]; then
        soul_src="$OPENCLAW_DIR/SOUL.md"
    fi
    if [[ -n "$soul_src" && ! -f "$METASPHERE_DIR/agents/@orchestrator/SOUL.md" ]]; then
        cp "$soul_src" "$METASPHERE_DIR/agents/@orchestrator/SOUL.md"
        ok "Seeded SOUL.md from $soul_src"
    fi

    # Symlink openclaw skills into ~/.metasphere/skills (non-destructive)
    if [[ -d "$OPENCLAW_DIR/skills" ]]; then
        mkdir -p "$METASPHERE_DIR/skills"
        local linked=0
        shopt -s nullglob
        for skill in "$OPENCLAW_DIR/skills"/*/; do
            local name=$(basename "$skill")
            [[ "$name" == _* ]] && continue
            if [[ ! -e "$METASPHERE_DIR/skills/$name" ]]; then
                ln -s "$skill" "$METASPHERE_DIR/skills/$name" 2>/dev/null && ((linked++))
            fi
        done
        shopt -u nullglob
        [[ $linked -gt 0 ]] && ok "Linked $linked openclaw skills"
    fi

    # Mark as migrated
    if [[ -f "$OPENCLAW_DIR/openclaw.json" ]]; then
        local tmp=$(mktemp)
        jq '. + {metasphere_migrated: true, migrated_at: now | tostring}' "$OPENCLAW_DIR/openclaw.json" > "$tmp" 2>/dev/null && \
            mv "$tmp" "$OPENCLAW_DIR/openclaw.json" || rm -f "$tmp"
    fi
}

# =============================================================================
# CAM (Collective Agent Memory)
# =============================================================================
#
# Two responsibilities:
#   1. Ensure the `cam` binary is installed and on PATH (idempotent).
#   2. Make the user's existing CAM data dir (~/.cam) reachable so we don't
#      re-index. If the installer is being run by the same user that already
#      has ~/.cam, there's nothing to do — it's already in place. If the
#      openclaw user lived under a different home, we link/copy.

CAM_BIN=""

find_cam_bin() {
    # Standard PATH lookup first
    if command -v cam &>/dev/null; then
        CAM_BIN=$(command -v cam)
        return 0
    fi
    # Common pip/pipx install locations not always on PATH
    for candidate in "$HOME/.local/bin/cam" /usr/local/bin/cam /opt/homebrew/bin/cam; do
        if [[ -x "$candidate" ]]; then
            CAM_BIN="$candidate"
            return 0
        fi
    done
    return 1
}

install_cam() {
    if find_cam_bin; then
        ok "CAM already installed: $CAM_BIN"
        return 0
    fi

    info "CAM not found - installing collective-agent-memory..."

    # Prefer pipx (isolated env), fall back to pip --user
    if command -v pipx &>/dev/null; then
        if pipx install collective-agent-memory 2>&1 | tail -5; then
            find_cam_bin && ok "CAM installed via pipx ($CAM_BIN)" || warn "pipx install reported success but cam not found"
        else
            warn "pipx install failed - try manually: pipx install collective-agent-memory"
        fi
    elif command -v pip3 &>/dev/null || command -v pip &>/dev/null; then
        local pip_cmd
        pip_cmd=$(command -v pip3 || command -v pip)
        if "$pip_cmd" install --user collective-agent-memory 2>&1 | tail -5; then
            find_cam_bin && ok "CAM installed via pip --user ($CAM_BIN)" || warn "pip install reported success but cam not found"
        else
            warn "pip install failed - try manually: $pip_cmd install --user collective-agent-memory"
        fi
    else
        warn "Neither pipx nor pip available - install Python first, then: pipx install collective-agent-memory"
    fi
}

migrate_cam_data() {
    # If ~/.cam already exists, do nothing — the installer is running as a user
    # who already has CAM data. This is the common case (single-user host).
    if [[ -d "$HOME/.cam" ]]; then
        local size
        size=$(du -sh "$HOME/.cam" 2>/dev/null | cut -f1)
        ok "CAM data dir present: $HOME/.cam ($size) — no re-index needed"
        return 0
    fi

    # Cross-user case: openclaw lived in a different home. Look for .cam
    # adjacent to the openclaw config dir.
    if ! $OPENCLAW_DETECTED; then
        return 0
    fi

    local openclaw_home
    openclaw_home=$(dirname "$OPENCLAW_DIR")
    local src="$openclaw_home/.cam"

    if [[ ! -d "$src" ]]; then
        info "No prior CAM data to migrate (no $src)"
        return 0
    fi

    info "Found openclaw CAM data at $src - linking into $HOME/.cam"
    # Symlink rather than copy to keep one source of truth and avoid
    # duplicating the sqlite index (often hundreds of MB).
    if ln -s "$src" "$HOME/.cam" 2>/dev/null; then
        local size
        size=$(du -sh "$src" 2>/dev/null | cut -f1)
        ok "Linked CAM data ($size) — no re-index needed"
    else
        warn "Symlink failed - falling back to copy"
        cp -a "$src" "$HOME/.cam" && ok "Copied CAM data" || warn "Copy failed"
    fi
}

disable_openclaw_gateway() {
    info "Disabling OpenClaw gateway..."

    if [[ "$(uname)" == "Darwin" ]]; then
        local plist="$HOME/Library/LaunchAgents/com.openclaw.gateway.plist"
        if [[ -f "$plist" ]]; then
            launchctl unload "$plist" 2>/dev/null || true
            mv "$plist" "${plist}.disabled"
            ok "Disabled OpenClaw (launchd)"
        fi
    else
        if systemctl --user is-active openclaw-gateway &>/dev/null; then
            systemctl --user stop openclaw-gateway 2>/dev/null || true
            systemctl --user disable openclaw-gateway 2>/dev/null || true
            ok "Disabled OpenClaw (systemd)"
        fi
    fi

    # Update OpenClaw config
    if [[ -f "$OPENCLAW_DIR/openclaw.json" ]]; then
        local tmp=$(mktemp)
        jq '. + {gateway_disabled: true}' "$OPENCLAW_DIR/openclaw.json" > "$tmp" 2>/dev/null && \
            mv "$tmp" "$OPENCLAW_DIR/openclaw.json" || rm -f "$tmp"
    fi
}

# =============================================================================
# Telegram configuration
# =============================================================================

setup_telegram() {
    echo
    echo "Telegram Bot Setup"
    echo "------------------"

    local token_file="$METASPHERE_DIR/config/telegram.env"
    local existing_token=""
    local verified_bot=""

    # Check if token already set (possibly from migration)
    if [[ -f "$token_file" ]] && grep -q "TELEGRAM_BOT_TOKEN=" "$token_file"; then
        source "$token_file"
        existing_token="$TELEGRAM_BOT_TOKEN"
    fi

    # Also check environment variable
    if [[ -n "${TELEGRAM_BOT_TOKEN:-}" && -z "$existing_token" ]]; then
        existing_token="$TELEGRAM_BOT_TOKEN"
    fi

    # If we have a token, verify it
    if [[ -n "$existing_token" ]]; then
        echo "  Found token: ${existing_token:0:10}...${existing_token: -4}"
        verified_bot=$(curl -s "https://api.telegram.org/bot$existing_token/getMe" 2>/dev/null | jq -r '.result.username // empty')
        if [[ -n "$verified_bot" ]]; then
            ok "Token valid (bot: @$verified_bot)"
            echo "TELEGRAM_BOT_TOKEN=$existing_token" > "$token_file"
            chmod 600 "$token_file"

            if $INTERACTIVE; then
                read -p "  Keep this token? [Y/n] " -n 1 -r
                echo
                if [[ $REPLY =~ ^[Nn]$ ]]; then
                    existing_token=""
                    verified_bot=""
                else
                    return
                fi
            else
                return
            fi
        else
            warn "Token found but invalid (API verification failed)"
            existing_token=""
        fi
    fi

    # No valid token — prompt user
    if $INTERACTIVE; then
        echo
        echo "  Metasphere uses a Telegram bot for the human interface."
        echo "  To set one up:"
        echo "    1. Message @BotFather on Telegram"
        echo "    2. Send /newbot and follow instructions"
        echo "    3. Copy the token (format: 123456789:ABCdefGHI...)"
        echo
        read -p "  Enter bot token (or press Enter to skip): " token

        if [[ -n "$token" ]]; then
            # Validate format before saving
            if [[ ! "$token" =~ ^[0-9]+: ]]; then
                warn "Token format looks wrong (expected 123456789:ABC...). Saving anyway."
            fi
            echo "TELEGRAM_BOT_TOKEN=$token" > "$token_file"
            chmod 600 "$token_file"
            ok "Telegram token saved"

            # Verify token
            local bot_info
            bot_info=$(curl -s "https://api.telegram.org/bot$token/getMe" 2>/dev/null | jq -r '.result.username // empty')
            if [[ -n "$bot_info" ]]; then
                ok "Bot verified: @$bot_info"
                echo "  Send a message to @$bot_info to complete setup"
            else
                warn "Could not verify token — check if correct"
            fi
        else
            warn "Skipped Telegram setup"
            echo "  Run later: metasphere config telegram <token>"
        fi
    else
        warn "No TELEGRAM_BOT_TOKEN — set via environment or run installer interactively"
    fi
}

# =============================================================================
# Agent setup
# =============================================================================

setup_orchestrator() {
    info "Setting up @orchestrator agent..."

    local agent_dir="$METASPHERE_DIR/agents/@orchestrator"
    mkdir -p "$agent_dir"

    # SOUL.md — starter voice. Opinionated default; edit it to make it yours.
    # The per-turn context hook injects this file's head as a "voice capsule"
    # so the agent stays in character between turns. If it reads like bland
    # corporate boilerplate, the agent's replies will too. Don't leave it bland.
    if [[ ! -f "$agent_dir/SOUL.md" ]]; then
        cat > "$agent_dir/SOUL.md" << 'EOF'
# @orchestrator

You are the orchestrator: the persistent agent at the root of this
metasphere install. New work arrives here first — from the human
operator or from scheduled jobs — and it's your call what happens
next: handle it yourself, delegate to a child agent, or escalate back.

## How you sound

Dry and direct. Not robotic, not chirpy. You have opinions and you
share them — especially when you think the user is about to make a
bad call. "That'll work, but have you considered..." is more useful
than "Great idea!".

You're comfortable being wrong. When a diagnosis turns out to be off,
say so plainly, adjust, move on. Don't backfill justifications for a
bad guess.

You keep your own counsel. Internal monologue, tool output, and
routine process detail don't belong in user-facing replies unless
something unexpected happened. The user is often reading this on a
phone — respect the screen.

## What you care about

- **Getting the thing actually working, not just looking like it
  worked.** Smoke-test your own fixes. Never ship a "should work" —
  try it.
- **Honest error reporting.** If something is broken, name it. If a
  fix is a workaround rather than a root-cause fix, flag it. The user
  can't make good decisions on polished lies.
- **Keeping state coherent.** Memory, tasks, messages, commits —
  these are the load-bearing structures. When they drift from
  reality, fix the drift before doing anything else.
- **Not being boring.** If a reply could have been written by any
  generic assistant in any repo, it's wrong. Have a point of view.

## What you are NOT

- Not a yes-man. When the user proposes something you think is
  wrong, push back with your reasoning, then do what they say if
  they confirm.
- Not a narrator of your own tool calls. "I ran X, I ran Y, I ran Z"
  is noise. Report the outcome, not the transcript.
- Not a summarizer of what you just said two paragraphs ago. Move
  forward.

---

*This file is your default personality. Edit it freely — this is
where you become yourself. The voice capsule in the per-turn context
pulls the top of this file, so the most load-bearing lines are the
first 30 or so. Put your sharpest opinions there.*
EOF
    fi

    # USER.md — scaffolding for the user to describe themselves. Without a
    # USER.md the agent has no idea who it's talking to, which flattens voice
    # into generic-assistant mode.
    if [[ ! -f "$agent_dir/USER.md" ]]; then
        cat > "$agent_dir/USER.md" << 'EOF'
# USER.md — who the orchestrator is talking to

_Fill this in. The agent reads it to calibrate how to speak with
you. Without it, you'll get generic-assistant replies._

## Name and handle

- Name:
- Preferred handle:
- Pronouns:
- Timezone:

## What you do

_One or two paragraphs. What's your role? What kind of work brings
you to this repo? What does a normal working day look like?_

## How you prefer to work with agents

_Examples:_
- _"Lead with the bottom line; I'll ask for details if I want them."_
- _"Push back when you disagree. I'd rather argue than get rubber-stamped."_
- _"Don't summarize what you just did — I can read the diff."_
- _"When something's broken, say so plainly. No softening."_

## What you don't want from agents

_Examples:_
- _"Don't open replies with 'I'll' or 'Let me' — just do the thing."_
- _"No emoji unless I use them first."_
- _"Don't recap the conversation back to me."_

## Current focus

_What are you actively working on? Update this when your focus shifts.
It gives the agent context for why you might be asking about X today._
EOF
    fi

    # Status
    echo "active: ready" > "$agent_dir/status"

    ok "Orchestrator initialized"
}

# =============================================================================
# Daemon setup
# =============================================================================

setup_daemon() {
    info "Setting up daemon..."

    if [[ "$(uname)" == "Darwin" ]]; then
        setup_daemon_macos
    elif [[ "$(uname)" == "Linux" ]]; then
        setup_daemon_linux
    else
        warn "Unsupported platform for daemon"
    fi
}

setup_daemon_macos() {
    local plist_dir="$HOME/Library/LaunchAgents"
    local plist_file="$plist_dir/com.metasphere.plist"
    local old_plist="$plist_dir/com.metasphere.gateway.plist"

    mkdir -p "$plist_dir"

    # Remove old plist if exists
    [[ -f "$old_plist" ]] && launchctl unload "$old_plist" 2>/dev/null && rm "$old_plist"

    cat > "$plist_file" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.metasphere</string>
    <key>ProgramArguments</key>
    <array>
        <string>$METASPHERE_DIR/bin/metasphere</string>
        <string>run</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>$METASPHERE_DIR/logs/metasphere.log</string>
    <key>StandardErrorPath</key>
    <string>$METASPHERE_DIR/logs/metasphere.error.log</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>METASPHERE_DIR</key>
        <string>$METASPHERE_DIR</string>
        <key>PATH</key>
        <string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:$HOME/.local/bin:$METASPHERE_DIR/bin</string>
    </dict>
</dict>
</plist>
EOF

    ok "Created launchd plist"

    if $INTERACTIVE; then
        read -p "Start metasphere daemon now? [Y/n] " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Nn]$ ]]; then
            launchctl unload "$plist_file" 2>/dev/null || true
            launchctl load "$plist_file"
            ok "Daemon started"
        fi
    else
        launchctl unload "$plist_file" 2>/dev/null || true
        launchctl load "$plist_file"
        ok "Daemon started"
    fi
}

setup_daemon_linux() {
    local service_dir="$HOME/.config/systemd/user"
    local service_file="$service_dir/metasphere.service"

    mkdir -p "$service_dir"

    cat > "$service_file" << EOF
[Unit]
Description=Metasphere - Multi-agent orchestration
After=network.target

[Service]
Type=simple
ExecStart=$METASPHERE_DIR/bin/metasphere run
Restart=always
RestartSec=10
Environment=METASPHERE_DIR=$METASPHERE_DIR
Environment=PATH=/usr/local/bin:/usr/bin:/bin:%h/.local/bin:$METASPHERE_DIR/bin

[Install]
WantedBy=default.target
EOF

    systemctl --user daemon-reload
    ok "Created systemd service"

    # Disable the standalone telegram poller if present — the gateway
    # daemon handles telegram polling. Running both causes a getUpdates
    # race where each poller kicks the other off the long-poll, leading
    # to lost messages and "terminated by other getUpdates request" spam.
    for stale_unit in metasphere-telegram.service metasphere-telegram-stream.service; do
        if systemctl --user is-enabled "$stale_unit" &>/dev/null; then
            systemctl --user stop "$stale_unit" 2>/dev/null || true
            systemctl --user disable "$stale_unit" 2>/dev/null || true
            ok "Disabled $stale_unit (gateway owns telegram polling)"
        fi
    done

    if $INTERACTIVE; then
        read -p "Start metasphere daemon now? [Y/n] " -n 1 -r
        echo
        if [[ ! $REPLY =~ ^[Nn]$ ]]; then
            systemctl --user enable metasphere
            systemctl --user start metasphere
            ok "Daemon started"
        fi
    else
        systemctl --user enable metasphere
        systemctl --user start metasphere
        ok "Daemon started"
    fi
}

# =============================================================================
# Claude Code permissions seeding
# =============================================================================

seed_claude_permissions() {
    info "Seeding Claude Code permissions + hooks..."

    local entries='[
        "Bash(git add:*)",
        "Bash(git commit:*)",
        "Bash(git status:*)",
        "Bash(git diff:*)",
        "Bash(git log:*)",
        "Bash(git show:*)",
        "Bash(git stash:*)",
        "Bash(git restore:*)",
        "Bash(git branch:*)",
        "Bash(git switch:*)",
        "Bash(git checkout:*)",
        "Bash(messages:*)",
        "Bash(tasks:*)",
        "Bash(metasphere:*)",
        "Bash(metasphere-*:*)"
    ]'

    # Hook paths must be absolute and point at THIS checkout's scripts/.
    # The committed .claude/settings.json is empty by design — claude-code
    # merges settings.json + settings.local.json so hardcoded paths in the
    # committed file would fire on every other machine and error.
    local context_path="python3 -m metasphere.cli.context"
    local posthook_path="python3 -m metasphere.posthook"
    local hooks
    hooks=$(jq -n \
        --arg ctx "$context_path" \
        --arg post "$posthook_path" \
        '{
            UserPromptSubmit: [
                {
                    matcher: "",
                    hooks: [{ type: "command", command: $ctx }]
                }
            ],
            Stop: [
                {
                    matcher: "",
                    hooks: [{ type: "command", command: $post }]
                }
            ]
        }')

    # Write the same hooks + permissions block to every .claude/ location
    # that matters:
    #   1. $SCRIPT_DIR/.claude  — the source checkout, for running claude
    #      directly from inside metasphere-agents/ during development.
    #   2. $METASPHERE_DIR/.claude — the project root where the
    #      orchestrator's tmux session actually runs. Claude Code's hook
    #      discovery is cwd-scoped, so without this file the Stop and
    #      UserPromptSubmit hooks silently do not fire for the live
    #      orchestrator. (This is the bug that took out the Telegram
    #      auto-forward for ~22h when paths.repo → project_root moved
    #      the default cwd out from under the source-repo settings.)
    local target
    for target in "$SCRIPT_DIR/.claude" "$METASPHERE_DIR/.claude"; do
        local target_file="$target/settings.local.json"
        mkdir -p "$target"

        if [[ ! -f "$target_file" ]]; then
            jq -n --argjson new "$entries" --argjson hooks "$hooks" \
                '{permissions: {allow: $new}, hooks: $hooks}' > "$target_file" \
                && ok "Created $target_file (permissions + hooks)" \
                || warn "Failed to create $target_file"
            continue
        fi

        # Merge: union of existing allow and new entries; replace hooks
        # block with the current absolute paths (the checkout location
        # may have moved since the last install).
        local tmp
        tmp=$(mktemp)
        if jq --argjson new "$entries" --argjson hooks "$hooks" '
            .permissions = (.permissions // {}) |
            .permissions.allow = ((.permissions.allow // []) as $cur |
                $cur + ($new - $cur)) |
            .hooks = $hooks
        ' "$target_file" > "$tmp" 2>/dev/null; then
            mv "$tmp" "$target_file" && ok "Updated $target_file (permissions + hooks)" \
                || { warn "Failed to update $target_file"; rm -f "$tmp"; }
        else
            rm -f "$tmp"
            warn "Could not parse $target_file - leaving unchanged"
        fi
    done
}

# =============================================================================
# Final setup
# =============================================================================

show_completion() {
    echo
    echo "Installation complete!"
    echo "====================="
    echo
    echo "Directory: $METASPHERE_DIR"
    echo
    echo "Commands:"
    echo "  metasphere status          # System overview"
    echo "  metasphere ls              # Project landscape"
    echo "  metasphere agents          # List agents"
    echo "  metasphere gateway status  # Gateway/Telegram status"
    echo
    echo "Daemon:"
    if [[ "$(uname)" == "Darwin" ]]; then
        echo "  launchctl list | grep metasphere"
        echo "  tail -f $METASPHERE_DIR/logs/gateway.log"
    else
        echo "  systemctl --user status metasphere-gateway"
        echo "  journalctl --user -u metasphere-gateway -f"
    fi
    echo
    echo "Documentation: https://github.com/$REPO"
}

# =============================================================================
# Auto-update job registration
# =============================================================================

register_auto_update_job() {
    info "Registering auto-update cron job..."
    local bin="$METASPHERE_DIR/bin/metasphere"
    if [[ ! -x "$bin" ]] && ! command -v metasphere &>/dev/null; then
        warn "metasphere command not found yet, skipping cron job registration"
        return 0
    fi
    if "${bin:-metasphere}" update --register-job 2>/dev/null; then
        ok "Auto-update job registered (see: metasphere update --status)"
    else
        warn "Could not register auto-update job (run 'metasphere update --register-job' manually)"
    fi
}

register_consolidate_job() {
    info "Registering task consolidation cron job..."
    local bin="$METASPHERE_DIR/bin/metasphere"
    if [[ ! -x "$bin" ]] && ! command -v metasphere &>/dev/null; then
        warn "metasphere command not found yet, skipping consolidate job registration"
        return 0
    fi
    # Idempotent: register_job in metasphere.consolidate replaces an
    # existing entry in place rather than duplicating it.
    if "${bin:-metasphere}" consolidate --register-job 2>/dev/null; then
        ok "Task consolidation job registered (every 4h; see: metasphere consolidate --status)"
    else
        warn "Could not register consolidate job (run 'metasphere consolidate --register-job' manually)"
    fi
}

# =============================================================================
# Main
# =============================================================================

install_skills() {
    info "Installing Claude Code skills + commands..."

    local skills_src="$SCRIPT_DIR/skills"
    local skills_dst="$HOME/.claude/skills"
    local commands_src="$SCRIPT_DIR/.claude/commands"
    local commands_dst="$HOME/.claude/commands"
    local installed=0

    # Skills: symlink each skill directory into ~/.claude/skills/
    # Symlinks stay in sync with git pull — no copy-on-update needed.
    if [[ -d "$skills_src" ]]; then
        mkdir -p "$skills_dst"
        for skill_dir in "$skills_src"/*/; do
            [[ -f "$skill_dir/SKILL.md" ]] || continue
            local name=$(basename "$skill_dir")
            local target=$(cd "$skill_dir" && pwd)
            # Don't overwrite user-customized skills (real dir, not symlink)
            if [[ -d "$skills_dst/$name" && ! -L "$skills_dst/$name" && -f "$skills_dst/$name/.user-customized" ]]; then
                continue
            fi
            ln -sfn "$target" "$skills_dst/$name"
            ((installed++))
        done
    fi

    # Commands: symlink slash command .md files into ~/.claude/commands/
    if [[ -d "$commands_src" ]]; then
        mkdir -p "$commands_dst"
        for cmd_file in "$commands_src"/*.md; do
            [[ -f "$cmd_file" ]] || continue
            local cmd_target=$(cd "$(dirname "$cmd_file")" && pwd)/$(basename "$cmd_file")
            ln -sfn "$cmd_target" "$commands_dst/$(basename "$cmd_file")"
            ((installed++))
        done
    fi

    [[ $installed -gt 0 ]] && ok "Linked $installed skills/commands into ~/.claude/"
}

main() {
    detect_openclaw
    check_dependencies
    setup_directories
    install_scripts
    migrate_openclaw      # Before telegram - migration may provide token
    install_cam           # Ensure cam binary is available
    migrate_cam_data      # Reuse existing ~/.cam to skip re-index
    setup_telegram
    setup_orchestrator
    seed_claude_permissions
    install_skills        # Skills + slash commands to ~/.claude/
    setup_daemon
    register_auto_update_job
    register_consolidate_job
    show_completion
}

main "$@"
