#!/usr/bin/env bash
# Prints one alert-state status bar per bundled theme. Used by themes.tape
# to render assets/themes.png, and handy on its own for eyeballing a palette.
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
WIDTH="${1:-72}"

printf '\n'
for palette in "$REPO"/themes/*.palette; do
    theme="$(basename "$palette" .palette)"
    printf '  \033[2m%-21s\033[0m' "$theme"
    "$REPO/bin/sentinel" preview -t "$theme" -w "$WIDTH" --sim alert \
        | sed -n '4p'
    printf '\n'
done
