#!/usr/bin/env bash
# ==============================================================================
# tmux-sentinel — plugin entrypoint (TPM-compatible, also fine to run by hand).
#
# Nothing here needs Python, and nothing here compiles anything. Building
# bin/sentinel-status belongs to `make install`; until it exists the bar renders
# in reduced mode from scripts/status-fallback.sh, and the next tmux start
# picks up the binary automatically.
#
# Sequence:
#   1. resolve the plugin directory from this file's own location
#   2. seed defaults (options.conf.default), then CLI/TUI settings (options.conf)
#      -- both with `set -ogq`, so an explicit `set -g @sentinel_*` in the
#      user's .tmux.conf always wins
#   3. migrate a v1 config.json once, if one is left over
#   4. generate sentinel.state and sentinel.conf
#   5. source the generated config and refresh clients
#
# Every failure is reported to the user and exits nonzero. Nothing is silenced.
# ==============================================================================

set -u

PROG=tmux-sentinel

self=${BASH_SOURCE[0]:-$0}
case $self in
*/*) ;;
*) self=./$self ;;
esac
hops=0
while [ -L "$self" ] && [ "$hops" -lt 32 ]; do
	link=$(readlink "$self") || break
	case $link in
	/*) self=$link ;;
	*) self=$(dirname "$self")/$link ;;
	esac
	hops=$((hops + 1))
done
PLUGIN_DIR=$(cd "$(dirname "$self")" && pwd -P) || {
	printf '%s: cannot determine the plugin directory\n' "$PROG" >&2
	exit 1
}

CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/tmux-sentinel"
USER_OPTIONS=$CONFIG_DIR/options.conf
LEGACY_JSON=$CONFIG_DIR/config.json
REDUCED_MARKER=$CONFIG_DIR/.reduced-mode-notified
GENERATED_CONF=$CONFIG_DIR/sentinel.conf

TAB=$(printf '\t')
NL=$(printf '\nx')
NL=${NL%x}

# TMUX_SENTINEL_SOCKET pins every tmux call to one server, so tests (and any
# user driving a private socket) never touch the default one.
tmux_cmd() {
	if [ -n "${TMUX_SENTINEL_SOCKET:-}" ]; then
		tmux -L "$TMUX_SENTINEL_SOCKET" "$@"
	else
		tmux "$@"
	fi
}

report() {
	printf '%s: %s\n' "$PROG" "$1" >&2
	tmux_cmd display-message -d 5000 "$PROG: $1" 2>/dev/null || true
}

fail() {
	report "$1"
	exit 1
}

if ! command -v tmux >/dev/null 2>&1; then
	printf '%s: tmux is not on PATH\n' "$PROG" >&2
	exit 1
fi

if ! mkdir -p "$CONFIG_DIR"; then
	fail "cannot create $CONFIG_DIR"
fi

# ------------------------------------------------------------ 2. seed options
if [ ! -f "$PLUGIN_DIR/options.conf.default" ]; then
	fail "options.conf.default is missing from $PLUGIN_DIR"
fi
if ! tmux_cmd source-file "$PLUGIN_DIR/options.conf.default"; then
	fail "tmux rejected options.conf.default"
fi
if [ -f "$USER_OPTIONS" ]; then
	if ! tmux_cmd source-file "$USER_OPTIONS"; then
		fail "tmux rejected $USER_OPTIONS; fix or delete that file"
	fi
fi

# --------------------------------------------------------------- 3. migration
# A v1 config.json becomes options.conf once, then is renamed out of the way.
# Values that cannot be represented safely are skipped rather than guessed at.
migrate_legacy_json() {
	if [ ! -f "$LEGACY_JSON" ] || [ -f "$USER_OPTIONS" ]; then
		return 0
	fi
	if ! command -v awk >/dev/null 2>&1 || ! command -v tr >/dev/null 2>&1; then
		report "found $LEGACY_JSON but awk/tr are unavailable; it was left in place"
		return 0
	fi

	local pairs
	# Braces and commas become newlines first, so both pretty-printed and
	# minified JSON reduce to one `"key": value` per line.
	# shellcheck disable=SC2020 # a character-for-character map is the intent
	pairs=$(tr '{},' '\n\n\n' <"$LEGACY_JSON" 2>/dev/null | awk '
		match($0, /"[A-Za-z_][A-Za-z0-9_]*"[ \t]*:/) {
			key = substr($0, RSTART + 1, RLENGTH - 1)
			sub(/"[ \t]*:$/, "", key)
			rest = substr($0, RSTART + RLENGTH)
			gsub(/^[ \t]+|[ \t]+$/, "", rest)
			if (rest == "") next
			if (rest ~ /^".*"$/) rest = substr(rest, 2, length(rest) - 2)
			print key "\t" rest
		}') || pairs=

	if [ -z "$pairs" ]; then
		report "could not parse $LEGACY_JSON; it was left in place and nothing was migrated"
		return 0
	fi

	# No arrays or associative arrays: macOS still ships bash 3.2, and TPM
	# users run whatever /usr/bin/env finds.
	local out=""
	local segments_seen=0
	local seg_off=" "
	local key value name
	local seg_names="thermal sleep_risk disk battery cpu memory multi_client clock"

	while IFS="$TAB" read -r key value; do
		[ -n "$key" ] || continue
		# Anything that could break out of a quoted tmux option is dropped.
		case $value in
		*'"'* | *"\\"* | *'$'* | *';'* | *'`'*)
			report "skipped $key from config.json: its value holds a forbidden character"
			continue
			;;
		esac
		case " $seg_names " in
		*" $key "*)
			segments_seen=1
			if [ "$value" != true ]; then
				seg_off="$seg_off$key "
			fi
			continue
			;;
		esac
		case $key in
		theme | position | interval | clock_format) name=$key ;;
		glyph_mode) name=glyphs ;;
		max_session_length) name=session_max_length ;;
		accent_symbol) name=accent ;;
		mode) name=windows ;;
		disk_warn_gb | disk_crit_gb | cpu_warn_pct | cpu_crit_pct) name=$key ;;
		battery_warn_pct | battery_crit_pct) name=$key ;;
		alerts_only)
			name=alerts_only
			case $value in
			true) value=on ;;
			*) value=off ;;
			esac
			;;
		*) continue ;;
		esac
		out="${out}set -ogq @sentinel_$name \"$value\"$NL"
	done <<EOF
$pairs
EOF

	if [ "$segments_seen" = 1 ]; then
		local enabled=""
		for name in $seg_names; do
			case $seg_off in
			*" $name "*) continue ;;
			esac
			if [ -n "$enabled" ]; then
				enabled="$enabled,$name"
			else
				enabled=$name
			fi
		done
		if [ -n "$enabled" ]; then
			out="${out}set -ogq @sentinel_segments \"$enabled\"$NL"
		fi
	fi

	if [ -z "$out" ]; then
		report "no recognisable settings in $LEGACY_JSON; it was left in place"
		return 0
	fi

	local tmp=$USER_OPTIONS.$$
	{
		printf '# tmux-sentinel options, migrated from config.json.\n'
		printf '# Managed by the sentinel CLI and TUI; hand edits are preserved\n'
		printf '# but may be rewritten when you change a setting from the TUI.\n'
		printf '%s' "$out"
	} >"$tmp" || {
		rm -f "$tmp"
		report "could not write $USER_OPTIONS; $LEGACY_JSON was left in place"
		return 0
	}
	if ! mv -f "$tmp" "$USER_OPTIONS"; then
		rm -f "$tmp"
		report "could not install $USER_OPTIONS; $LEGACY_JSON was left in place"
		return 0
	fi
	if ! mv -f "$LEGACY_JSON" "$LEGACY_JSON.migrated"; then
		report "migrated settings to $USER_OPTIONS but could not rename $LEGACY_JSON"
	else
		report "migrated config.json to options.conf (old file kept as config.json.migrated)"
	fi
	if ! tmux_cmd source-file "$USER_OPTIONS"; then
		fail "tmux rejected the migrated $USER_OPTIONS"
	fi
	return 0
}

migrate_legacy_json

# -------------------------------------------------------------- 4. generation
if [ ! -x "$PLUGIN_DIR/scripts/generate.sh" ]; then
	fail "scripts/generate.sh is missing or not executable in $PLUGIN_DIR"
fi
if ! "$PLUGIN_DIR/scripts/generate.sh"; then
	fail "scripts/generate.sh failed; the status bar was left unchanged"
fi

# ------------------------------------------------------------------ 5. install
if [ ! -f "$GENERATED_CONF" ]; then
	fail "scripts/generate.sh reported success but $GENERATED_CONF is absent"
fi
if ! tmux_cmd source-file "$GENERATED_CONF"; then
	fail "tmux rejected $GENERATED_CONF"
fi
# refresh-client needs a client; a server with none attached is a normal state
# (headless `sentinel apply`, or a server whose last client just detached), not
# a failure. The generated config is already loaded either way.
if [ -n "$(tmux_cmd list-clients -F '#{client_name}' 2>/dev/null)" ]; then
	if ! tmux_cmd refresh-client -S; then
		fail "tmux refresh-client failed"
	fi
fi

# Reduced mode is worth saying once, not on every tmux start.
if [ -x "$PLUGIN_DIR/bin/sentinel-status" ]; then
	rm -f "$REDUCED_MARKER"
else
	if [ ! -f "$REDUCED_MARKER" ]; then
		report "running in reduced mode; run 'make' in $PLUGIN_DIR for the full bar"
		: >"$REDUCED_MARKER" || true
	fi
fi

exit 0
