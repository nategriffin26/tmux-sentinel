#!/usr/bin/env bash
# ==============================================================================
# tmux-sentinel: Minimal, alerts-only tmux status bar plugin entrypoint.
# Compatible with TPM (Tmux Plugin Manager) and standalone execution.
# ==============================================================================

CURRENT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/tmux-sentinel"
CONF_FILE="$CONFIG_DIR/sentinel.conf"
ENV_FILE="$CONFIG_DIR/env.sh"

# Ensure config directory exists
mkdir -p "$CONFIG_DIR"

# Build mac-cpu-pct if on Darwin and not already compiled
if [ "$(uname -s)" = "Darwin" ] && [ ! -f "$CURRENT_DIR/bin/mac-cpu-pct" ]; then
    if command -v clang >/dev/null 2>&1 || command -v cc >/dev/null 2>&1; then
        make -C "$CURRENT_DIR/src" >/dev/null 2>&1 || true
    fi
fi

# If sentinel.conf or env.sh doesn't exist, generate them using Python CLI or fallback
if [ ! -f "$CONF_FILE" ] || [ ! -f "$ENV_FILE" ]; then
    if command -v python3 >/dev/null 2>&1; then
        python3 "$CURRENT_DIR/cli/main.py" generate >/dev/null 2>&1 || true
    fi
fi

# If still missing (e.g. no python3), create standard fallback
if [ ! -f "$CONF_FILE" ]; then
    cat << 'EOF' > "$CONF_FILE"
set -g status on
set -g status-position top
set -g status-interval 10
set -g status-justify left
set -g status-style "bg=default,fg=#cdd6f4"
set -g window-status-format ""
set -g window-status-current-format ""
set -g window-status-separator ""
set -g status-left-length 30
set -g status-left "#[fg=#{?client_prefix,#f38ba8,#{?pane_in_mode,#f9e2af,#89b4fa}}]▌#[fg=#cdd6f4,bold] #{=/18/…:session_name} #[default]"
set -g status-right-length 100
set -g status-right "#(~/.config/tmux-sentinel/status-right.sh)#{?#{e|>:#{session_attached},1},#[fg=#94e2d5] #{session_attached}#[fg=#45475a] · ,}#[fg=#cdd6f4,bold]%H:%M "
set -g message-style "bg=#313244,fg=#cdd6f4"
set -g message-command-style "bg=#313244,fg=#f9e2af"
set -g mode-style "bg=#45475a,fg=#cdd6f4"
set -g pane-border-style "fg=#313244"
set -g pane-active-border-style "fg=#89b4fa"
EOF
fi

# Source configuration into tmux
tmux source-file "$CONF_FILE" 2>/dev/null || true
tmux refresh-client -S 2>/dev/null || true
