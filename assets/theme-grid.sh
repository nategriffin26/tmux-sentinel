#!/usr/bin/env bash
# Prints one alert-state status bar per bundled theme. Used by themes.tape
# to render assets/themes.png, and handy on its own for eyeballing a palette.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WIDTH="${1:-72}"

# Render against a throwaway config so the output is identical on any machine
# and cannot be perturbed by whatever the author happens to have set.
SCRATCH="$(mktemp -d "${TMPDIR:-/tmp}/sentinel-grid.XXXXXX")"
trap 'rm -rf "$SCRATCH"' EXIT
export XDG_CONFIG_HOME="$SCRATCH/config"
mkdir -p "$XDG_CONFIG_HOME"

printf '\n'
for palette in "$REPO"/themes/*.palette; do
    theme="$(basename "$palette" .palette)"
    printf '  \033[2m%-21s\033[0m' "$theme"
    "$REPO/bin/sentinel" preview -t "$theme" -w "$WIDTH" --sim alert \
        | sed -n '4p'
    printf '\n'
done
