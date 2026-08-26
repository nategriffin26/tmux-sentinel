#!/bin/sh
# Reload tmux statusbar and client display
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/tmux-sentinel"
CONF_FILE="$CONFIG_DIR/sentinel.conf"

if [ -f "$CONF_FILE" ]; then
    tmux source-file "$CONF_FILE" 2>/dev/null || true
    tmux refresh-client -S 2>/dev/null || true
    echo "tmux-sentinel: reloaded successfully."
else
    echo "tmux-sentinel: config not found at $CONF_FILE." >&2
    exit 1
fi
