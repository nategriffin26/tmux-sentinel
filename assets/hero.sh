#!/usr/bin/env bash
# Renders the GitHub social-preview card body. Used by assets/hero.tape.
#
# Ends in a long sleep so the shell prompt never reappears in the capture.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
S="$REPO/bin/sentinel"

# Render against a throwaway config so the card is identical on any machine and
# cannot be perturbed by the author's own settings — or by a diagnostic those
# settings provoke on stderr.
SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/sentinel-hero.XXXXXX")"
trap 'rm -rf "$SCRATCH"' EXIT
export XDG_CONFIG_HOME="$SCRATCH/config"
mkdir -p "$XDG_CONFIG_HOME"

DIM=$'\033[38;2;108;112;134m'
TXT=$'\033[1;38;2;205;214;244m'
ACC=$'\033[38;2;137;180;250m'
OFF=$'\033[0m'

bar() { "$S" preview -t catppuccin-mocha -w 76 --sim "$1" 2>/dev/null | sed -n '4p'; }

printf '\n\n\n\n\n'
printf '    %s▌%s %stmux-sentinel%s\n\n' "$ACC" "$OFF" "$TXT" "$OFF"
printf '    %sA quiet-by-default host health watchdog for your tmux status bar.%s\n' "$DIM" "$OFF"
printf '\n\n'
printf '    %shealthy: free space, cpu, memory, clock. nothing else.%s\n' "$DIM" "$OFF"
printf '    '; bar healthy
printf '\n'
printf '    %sthrottling · sleep armed · disk low · battery draining%s\n' "$DIM" "$OFF"
printf '    '; bar alert
printf '\n\n\n'
printf '    %s0 forks per tick   ·   5.4 ms   ·   12 themes   ·   macOS + Linux%s\n' "$DIM" "$OFF"

sleep 300
