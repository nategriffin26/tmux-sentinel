#!/usr/bin/env bash
# Boots an isolated tmux session with tmux-sentinel loaded, for VHS recording.
# Never touches the user's real config, real HOME, or default tmux server.
#
#   ./assets/demo-session.sh [option=value ...]
#
# Each argument sets a @sentinel_<option>, e.g. `theme=nord glyphs=ascii`.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOCKET="${SENTINEL_DEMO_SOCKET:-sentinel-demo}"
SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/sentinel-demo.XXXXXX")"

export HOME="$SCRATCH"
export XDG_CONFIG_HOME="$SCRATCH/config"
export PATH="$REPO/bin:$PATH"
mkdir -p "$XDG_CONFIG_HOME"

# A minimal rcfile keeps the recorded prompt short and stable across machines.
cat >"$SCRATCH/bashrc" <<RC
export PATH="$REPO/bin:\$PATH"
export HOME="$SCRATCH"
export XDG_CONFIG_HOME="$XDG_CONFIG_HOME"
export PS1='\[\e[38;5;110m\]~\[\e[0m\] \$ '
cd "$SCRATCH"
RC

tmux() { command tmux -L "$SOCKET" -f /dev/null "$@"; }

tmux kill-server 2>/dev/null || true
tmux new-session -d -s demo -c "$SCRATCH" \
    "bash --noprofile --rcfile '$SCRATCH/bashrc' -i"
tmux set-option -g status-interval 1

for opt in "$@"; do
    tmux set-option -g "@sentinel_${opt%%=*}" "${opt#*=}"
done

tmux run-shell "$REPO/sentinel.tmux"

# `exec` skips shell functions, so the socket flags must be spelled out here
# or this silently attaches to the user's real tmux server.
exec command tmux -L "$SOCKET" -f /dev/null attach -t demo
