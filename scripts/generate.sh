#!/bin/sh
# ==============================================================================
# tmux-sentinel — the sole generator.
#
# Reads @sentinel_* options (options.conf.default, then ~/.config/tmux-sentinel/
# options.conf, then the live tmux server), reads themes/<stem>.palette and
# glyphs/<mode>.glyphs, and atomically writes two artifacts into
# ${XDG_CONFIG_HOME:-$HOME/.config}/tmux-sentinel/:
#
#   sentinel.state   key=value facts, parsed by sentinel-status every tick.
#   sentinel.conf    tmux config, sourced once per tmux start.
#
# Exit 0: both artifacts written. A value outside its documented domain is
#         replaced by its default and reported, which is not an error.
# Exit 1: nothing was written; existing artifacts are left untouched.
#
# Every value reaching a generated file is domain-validated first. Neither
# artifact is ever shell-sourced: sentinel.state is read by a parser and
# sentinel.conf contains only tmux `set` commands built from validated pieces.
# ==============================================================================
#
# Options, palette entries and glyphs live in three parallel variable
# namespaces (def_*, opt_*, pal_*, gly_*, segon_*) that are populated by name
# through `eval`, which is how one parser serves every key without an
# associative array. ShellCheck cannot see those assignments, hence the
# file-wide SC2154 exemption.
# shellcheck disable=SC2154

set -u

PROG=tmux-sentinel

CR=$(printf '\r')
LF=$(printf '\nx')
LF=${LF%x}
TAB=$(printf '\t')

OPTION_NAMES='theme position interval glyphs always segments windows clock_format session_max_length accent disk_warn_gb disk_crit_gb cpu_warn_pct cpu_crit_pct battery_warn_pct battery_crit_pct memory_warn_pct memory_crit_pct'
SEGMENT_NAMES='thermal sleep_risk disk battery cpu memory multi_client clock'
PALETTE_COLOURS='bg fg dim val sep accent prefix copy_mode warn alert peach info border active_border message_bg mode_bg'
GLYPH_KEYS='accent sep thermal sleep disk battery_full battery_mid battery_low cpu memory clients'

# --------------------------------------------------------------- locate ourself
# The plugin directory is derived from this script's own resolved location on
# every run, never from a stored value, so a moved checkout self-heals.
self=$0
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
SCRIPT_DIR=$(cd "$(dirname "$self")" && pwd -P) || exit 1
PLUGIN_DIR=$(cd "$SCRIPT_DIR/.." && pwd -P) || exit 1

CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/tmux-sentinel"
DEFAULTS_FILE=$PLUGIN_DIR/options.conf.default
USER_OPTIONS=$CONFIG_DIR/options.conf
STATE_FILE=$CONFIG_DIR/sentinel.state
CONF_FILE=$CONFIG_DIR/sentinel.conf

# TMUX_SENTINEL_SOCKET pins every tmux call to one server, so tests (and any
# user driving a private socket) never touch the default one.
tmux_cmd() {
	if [ -n "${TMUX_SENTINEL_SOCKET:-}" ]; then
		tmux -L "$TMUX_SENTINEL_SOCKET" "$@"
	else
		tmux "$@"
	fi
}

have_server=0
if command -v tmux >/dev/null 2>&1 && tmux_cmd show-options -g >/dev/null 2>&1; then
	have_server=1
fi

tmp_state=
tmp_conf=
# Invoked from the trap below, so a failed run leaves no half-written temp file.
# ShellCheck cannot see trap-driven calls: older versions flag the body as
# unreachable (SC2317), newer ones flag the function as unused (SC2329).
# shellcheck disable=SC2317,SC2329
cleanup() {
	if [ -n "$tmp_state" ]; then rm -f "$tmp_state"; fi
	if [ -n "$tmp_conf" ]; then rm -f "$tmp_conf"; fi
}
trap cleanup EXIT HUP INT TERM

notify() {
	printf '%s: %s\n' "$PROG" "$1" >&2
	if [ "$have_server" = 1 ]; then
		tmux_cmd display-message -d 4000 "$PROG: $1" 2>/dev/null || true
	fi
}

die() {
	notify "$1"
	exit 1
}

# Character count, not byte count, so a multi-byte accent mark measures as one.
str_len() {
	_n=$(printf '%s' "$1" | LC_ALL=C.UTF-8 wc -m 2>/dev/null | tr -d ' ')
	case $_n in
	'' | *[!0-9]*) _n=$(printf '%s' "$1" | wc -c | tr -d ' ') ;;
	esac
	printf '%s' "$_n"
}

# Strip leading and trailing spaces and tabs, without forking.
trim_result=
trim() {
	trim_result=$1
	while :; do
		case $trim_result in
		' '*) trim_result=${trim_result# } ;;
		"$TAB"*) trim_result=${trim_result#"$TAB"} ;;
		*) break ;;
		esac
	done
	while :; do
		case $trim_result in
		*' ') trim_result=${trim_result% } ;;
		*"$TAB") trim_result=${trim_result%"$TAB"} ;;
		*) break ;;
		esac
	done
}

# Parse `set -ogq @sentinel_<name> "<value>"` lines into <prefix>_<name> shell
# variables. Only names in OPTION_NAMES are accepted; anything else is ignored.
parse_options_file() {
	_file=$1
	_prefix=$2
	[ -f "$_file" ] || return 0
	while IFS= read -r _line || [ -n "$_line" ]; do
		_line=${_line%"$CR"}
		trim "$_line"
		_line=$trim_result
		case $_line in
		'' | '#'*) continue ;;
		*@sentinel_*) ;;
		*) continue ;;
		esac
		_rest=${_line#*@sentinel_}
		_name=${_rest%%[!a-z0-9_]*}
		case " $OPTION_NAMES " in
		*" $_name "*) ;;
		*) continue ;;
		esac
		_val=${_rest#"$_name"}
		trim "$_val"
		_val=$trim_result
		case $_val in
		'"'*'"')
			_val=${_val#\"}
			_val=${_val%\"}
			;;
		"'"*"'")
			_val=${_val#\'}
			_val=${_val%\'}
			;;
		esac
		eval "${_prefix}_${_name}=\$_val"
	done <"$_file"
}

# True when an options file assigns @sentinel_<name>, whether or not the name is
# still a recognised option. Used to notice a removed option that
# parse_options_file deliberately ignores.
file_sets_option() {
	_file=$1
	_want=$2
	[ -f "$_file" ] || return 1
	while IFS= read -r _line || [ -n "$_line" ]; do
		_line=${_line%"$CR"}
		trim "$_line"
		_line=$trim_result
		case $_line in
		'' | '#'*) continue ;;
		*@sentinel_*) ;;
		*) continue ;;
		esac
		_rest=${_line#*@sentinel_}
		if [ "${_rest%%[!a-z0-9_]*}" = "$_want" ]; then
			return 0
		fi
	done <"$_file"
	return 1
}

# Parse a `key=value` data file verbatim: no trimming and no unquoting, so a
# padded value such as ` · ` survives byte for byte.
parse_kv_file() {
	_file=$1
	_prefix=$2
	[ -f "$_file" ] || return 1
	while IFS= read -r _line || [ -n "$_line" ]; do
		_line=${_line%"$CR"}
		case $_line in
		'' | '#'*) continue ;;
		*=*) ;;
		*) continue ;;
		esac
		_key=${_line%%=*}
		case $_key in
		'' | *[!a-z0-9_]*) continue ;;
		esac
		_val=${_line#*=}
		eval "${_prefix}_${_key}=\$_val"
	done <"$_file"
}

# ------------------------------------------------------------------ 1. defaults
[ -f "$DEFAULTS_FILE" ] ||
	die "missing $DEFAULTS_FILE; the plugin checkout is incomplete"
parse_options_file "$DEFAULTS_FILE" def
for name in $OPTION_NAMES; do
	eval "probe=\${def_$name-__absent__}"
	if [ "$probe" = __absent__ ]; then
		die "options.conf.default declares no default for @sentinel_$name"
	fi
	eval "opt_$name=\$def_$name"
done

# --------------------------------------------------------------- 2. user layers
# Lowest to highest: defaults, CLI/TUI-written options.conf, live tmux options
# (which already include anything set explicitly in the user's .tmux.conf).
parse_options_file "$USER_OPTIONS" opt

if [ "$have_server" = 1 ]; then
	for name in $OPTION_NAMES; do
		live=$(tmux_cmd show-option -gqv "@sentinel_$name" 2>/dev/null) || live=
		if [ -n "$live" ]; then
			eval "opt_$name=\$live"
		fi
	done
fi

# -------------------------------------------------------- 2b. removed options
# @sentinel_alerts_only was one boolean standing in for eight independent
# decisions and was removed in 0.3.0. It is reported rather than translated:
# quietly dropping a setting the user wrote is the "knob that lies" defect this
# project has already fixed once. The live server is asked first because a
# .tmux.conf setting only ever shows up there; options.conf is scanned too so
# the warning still fires when generate.sh runs with no server.
dead_set=0
if [ "$have_server" = 1 ] &&
	[ -n "$(tmux_cmd show-option -gqv @sentinel_alerts_only 2>/dev/null)" ]; then
	dead_set=1
fi
if [ "$dead_set" = 0 ] && file_sets_option "$USER_OPTIONS" alerts_only; then
	dead_set=1
fi
if [ "$dead_set" = 1 ]; then
	notify '@sentinel_alerts_only was removed in 0.3.0 and is ignored. Use @sentinel_always: set -g @sentinel_always "disk,cpu,memory,clock" for the old "on", or list all eight segments for the old "off".'
fi

# ---------------------------------------------------------------- 3. validation
# Domain enforcement is the security boundary. An out-of-domain value is
# replaced by its default and reported; it never reaches a generated file.
reject() {
	eval "_d=\$def_$1"
	eval "opt_$1=\$_d"
	notify "@sentinel_$1 rejected: $2. Using default \"$_d\"."
}

val_of() { eval "v=\$opt_$1"; }

# CR or LF in a value is refused outright: a newline in a tmux option is how
# arbitrary tmux commands were injected into the generated config in v1.
for name in $OPTION_NAMES; do
	val_of "$name"
	case $v in
	*"$CR"* | *"$LF"*) reject "$name" "contains a carriage return or newline" ;;
	esac
done

check_int() {
	val_of "$1"
	case $v in
	'' | *[!0-9]*)
		reject "$1" "must be an integer"
		return
		;;
	esac
	if [ "${#v}" -gt 6 ] || [ "$v" -lt "$2" ] || [ "$v" -gt "$3" ]; then
		reject "$1" "must be an integer in $2..$3"
	fi
}

check_enum() {
	_ename=$1
	shift
	val_of "$_ename"
	for _cand in "$@"; do
		if [ "$v" = "$_cand" ]; then
			return
		fi
	done
	reject "$_ename" "must be one of: $*"
}

# A segment list is a comma-separated selection from the eight known names with
# no spaces. Two options share this grammar: @sentinel_segments (which segments
# exist at all) and @sentinel_always (which of them keep a visible resting
# state). A user wanting nothing extra at rest writes a segment that has no
# quiet state anyway, e.g. "cpu"; the empty list is not in the domain.
check_segment_list() {
	val_of "$1"
	_lbad=0
	case $v in
	'' | *[!a-z_,]*) _lbad=1 ;;
	,* | *, | *,,*) _lbad=1 ;;
	esac
	if [ "$_lbad" = 0 ]; then
		_lifs=$IFS
		IFS=,
		for _ls in $v; do
			case " $SEGMENT_NAMES " in
			*" $_ls "*) ;;
			*) _lbad=1 ;;
			esac
		done
		IFS=$_lifs
	fi
	if [ "$_lbad" = 1 ]; then
		reject "$1" "must be a comma-separated list, no spaces, drawn from: $SEGMENT_NAMES"
	fi
}

# A data-file name must be a bare [a-z0-9-]+ stem resolving to a real file: no
# separators and no dots, therefore no traversal out of themes/ or glyphs/.
check_data_name() {
	_dname=$1
	_dir=$2
	_ext=$3
	val_of "$_dname"
	case $v in
	'' | *[!a-z0-9-]*)
		reject "$_dname" "must match [a-z0-9-]+"
		val_of "$_dname"
		;;
	esac
	if [ ! -f "$PLUGIN_DIR/$_dir/$v.$_ext" ]; then
		reject "$_dname" "$_dir/$v.$_ext does not exist"
		val_of "$_dname"
		if [ ! -f "$PLUGIN_DIR/$_dir/$v.$_ext" ]; then
			die "default $_dir/$v.$_ext is missing; the plugin checkout is incomplete"
		fi
	fi
}

check_data_name theme themes palette
check_enum glyphs nerd unicode ascii
check_data_name glyphs glyphs glyphs
check_enum position top bottom
check_enum windows hidden minimal tabs
check_int interval 1 3600
check_int session_max_length 1 64
check_int disk_warn_gb 0 100000
check_int disk_crit_gb 0 100000
check_int cpu_warn_pct 0 100
check_int cpu_crit_pct 0 100
check_int battery_warn_pct 0 100
check_int battery_crit_pct 0 100
check_int memory_warn_pct 0 100
check_int memory_crit_pct 0 100

# clock_format: strftime conversions, alphanumerics, spaces and `: / . , + -`.
# Quotes, backslashes, `$`, `;`, `#` and control characters are all excluded.
val_of clock_format
clock_bad=0
case $v in
*[!%A-Za-z0-9\ :/.,+-]*) clock_bad=1 ;;
esac
if [ "${#v}" -lt 1 ] || [ "${#v}" -gt 32 ]; then
	clock_bad=1
fi
if [ "$clock_bad" = 1 ]; then
	reject clock_format 'must be 1..32 characters matching ^[%A-Za-z0-9 :/.,+-]{1,32}$'
fi

# @sentinel_segments decides which segments exist at all; @sentinel_always
# decides which of those keep a visible resting state.
check_segment_list segments
check_segment_list always

# accent: empty means "inherit the glyph set's mark"; otherwise 1..4 characters
# with every tmux- and shell-significant character excluded.
val_of accent
if [ -n "$v" ]; then
	accent_bad=0
	case $v in
	*'"'* | *"'"* | *"\\"* | *'$'* | *';'* | *'#'* | *'`'*) accent_bad=1 ;;
	esac
	accent_len=$(str_len "$v")
	if [ "$accent_len" -lt 1 ] || [ "$accent_len" -gt 4 ]; then
		accent_bad=1
	fi
	if [ "$accent_bad" = 1 ]; then
		reject accent 'must be empty, or 1..4 characters excluding " '"'"' \ $ ; # `'
	fi
fi

# ---------------------------------------------------------------- 4. data files
parse_kv_file "$PLUGIN_DIR/themes/$opt_theme.palette" pal ||
	die "cannot read themes/$opt_theme.palette"
parse_kv_file "$PLUGIN_DIR/glyphs/$opt_glyphs.glyphs" gly ||
	die "cannot read glyphs/$opt_glyphs.glyphs"

# Palette colours land in tmux config, so a hand-edited palette gets the same
# scrutiny as a user option.
for key in $PALETTE_COLOURS; do
	eval "colour=\${pal_$key-}"
	case $colour in
	'' | *[!a-zA-Z0-9#]*)
		die "themes/$opt_theme.palette: '$key' is missing or is not a colour"
		;;
	esac
done

for key in $GLYPH_KEYS; do
	eval "glyph=\${gly_$key-__absent__}"
	if [ "$glyph" = __absent__ ]; then
		die "glyphs/$opt_glyphs.glyphs: '$key' is missing"
	fi
done

case $gly_accent in
'' | *'"'* | *"'"* | *"\\"* | *'$'* | *';'* | *'#'* | *'`'*)
	die "glyphs/$opt_glyphs.glyphs: 'accent' is empty or holds a forbidden character"
	;;
esac

accent_mark=$opt_accent
if [ -z "$accent_mark" ]; then
	accent_mark=$gly_accent
fi

# ------------------------------------------------------------- 5. derived facts
for s in $SEGMENT_NAMES; do
	eval "segon_$s=0"
	eval "always_$s=0"
done
old_ifs=$IFS
IFS=,
for s in $opt_segments; do
	eval "segon_$s=1"
done
for s in $opt_always; do
	eval "always_$s=1"
done
IFS=$old_ifs

# status-right is rebuilt on every run, so building the binary later re-points
# the bar at it with no further action from the user.
case $PLUGIN_DIR in
*"'"* | *'"'* | *"\\"* | *'$'* | *'`'* | *'#'* | *"$LF"* | *"$CR"*)
	die "the plugin path holds a character that cannot be quoted safely: $PLUGIN_DIR"
	;;
esac

if [ -x "$PLUGIN_DIR/bin/sentinel-status" ]; then
	status_cmd="'$PLUGIN_DIR/bin/sentinel-status' --clients #{session_attached}"
else
	if [ ! -x "$PLUGIN_DIR/scripts/status-fallback.sh" ]; then
		die "neither bin/sentinel-status nor scripts/status-fallback.sh is executable"
	fi
	status_cmd="'$PLUGIN_DIR/scripts/status-fallback.sh'"
fi

# -------------------------------------------------------------------- 6. writing
mkdir -p "$CONFIG_DIR" || die "cannot create $CONFIG_DIR"

emit() { printf '%s=%s\n' "$1" "$2"; }

tmp_state=$CONFIG_DIR/.sentinel.state.$$
tmp_conf=$CONFIG_DIR/.sentinel.conf.$$

{
	printf '# tmux-sentinel state - generated by scripts/generate.sh, do not edit.\n'
	printf '# Theme: %s | Glyphs: %s\n' "$pal_name" "$gly_name"
	emit version 1
	printf '\n'
	emit always_thermal "$always_thermal"
	emit always_sleep_risk "$always_sleep_risk"
	emit always_disk "$always_disk"
	emit always_battery "$always_battery"
	emit always_cpu "$always_cpu"
	emit always_memory "$always_memory"
	emit always_multi_client "$always_multi_client"
	emit always_clock "$always_clock"
	emit clock_format "$opt_clock_format"
	printf '\n'
	emit color_fg "$pal_fg"
	emit color_dim "$pal_dim"
	emit color_val "$pal_val"
	emit color_sep "$pal_sep"
	emit color_alert "$pal_alert"
	emit color_warn "$pal_warn"
	emit color_peach "$pal_peach"
	emit color_info "$pal_info"
	printf '\n'
	emit seg_thermal "$segon_thermal"
	emit seg_sleep_risk "$segon_sleep_risk"
	emit seg_disk "$segon_disk"
	emit seg_battery "$segon_battery"
	emit seg_cpu "$segon_cpu"
	emit seg_memory "$segon_memory"
	emit seg_multi_client "$segon_multi_client"
	emit seg_clock "$segon_clock"
	printf '\n'
	emit glyph_sep "$gly_sep"
	emit glyph_thermal "$gly_thermal"
	emit glyph_sleep "$gly_sleep"
	emit glyph_disk "$gly_disk"
	emit glyph_battery_full "$gly_battery_full"
	emit glyph_battery_mid "$gly_battery_mid"
	emit glyph_battery_low "$gly_battery_low"
	emit glyph_cpu "$gly_cpu"
	emit glyph_memory "$gly_memory"
	emit glyph_clients "$gly_clients"
	printf '\n'
	emit disk_warn_gb "$opt_disk_warn_gb"
	emit disk_crit_gb "$opt_disk_crit_gb"
	emit cpu_warn_pct "$opt_cpu_warn_pct"
	emit cpu_crit_pct "$opt_cpu_crit_pct"
	emit battery_warn_pct "$opt_battery_warn_pct"
	emit battery_crit_pct "$opt_battery_crit_pct"
	emit memory_warn_pct "$opt_memory_warn_pct"
	emit memory_crit_pct "$opt_memory_crit_pct"
} >"$tmp_state" || die "cannot write $tmp_state"

{
	printf '# tmux-sentinel configuration - generated by scripts/generate.sh.\n'
	printf '# Theme: %s (%s)\n' "$pal_name" "$opt_theme"
	printf '# Regenerated on every tmux start; edits here are lost.\n'
	printf '\n'
	printf 'set -g status on\n'
	printf 'set -g status-position %s\n' "$opt_position"
	printf 'set -g status-interval %s\n' "$opt_interval"
	printf 'set -g status-justify left\n'
	printf 'set -g status-style "bg=%s,fg=%s"\n' "$pal_bg" "$pal_fg"
	printf '\n'
	printf '# Window list (@sentinel_windows = %s)\n' "$opt_windows"
	case $opt_windows in
	hidden)
		printf 'set -g window-status-format ""\n'
		printf 'set -g window-status-current-format ""\n'
		printf 'set -g window-status-separator ""\n'
		;;
	minimal)
		printf 'set -g window-status-format "#[fg=%s] #I:#W "\n' "$pal_dim"
		printf 'set -g window-status-current-format "#[fg=%s,bold] #I:#W "\n' "$pal_accent"
		printf 'set -g window-status-separator "#[fg=%s]|"\n' "$pal_sep"
		;;
	tabs)
		printf 'set -g window-status-format "#[fg=%s,bg=%s] #I #[fg=%s,bg=%s] #W "\n' \
			"$pal_dim" "$pal_border" "$pal_fg" "$pal_message_bg"
		printf 'set -g window-status-current-format "#[fg=%s,bg=%s,bold] #I #[fg=%s,bg=%s] #W "\n' \
			"$pal_bg" "$pal_accent" "$pal_fg" "$pal_mode_bg"
		printf 'set -g window-status-separator " "\n'
		;;
	esac
	printf '\n'
	printf '# Status left: mode-aware accent mark plus session identity.\n'
	printf 'set -g status-left-length %s\n' "$((opt_session_max_length + 16))"
	printf 'set -g status-left "#[fg=#{?client_prefix,%s,#{?pane_in_mode,%s,%s}}]%s#[fg=%s,bold] #{=/%s/…:session_name} #[default]"\n' \
		"$pal_prefix" "$pal_copy_mode" "$pal_accent" "$accent_mark" \
		"$pal_fg" "$opt_session_max_length"
	printf '\n'
	printf '# Status right: one implementation renders the whole thing.\n'
	printf 'set -g status-right-length 100\n'
	printf 'set -g status-right "#(%s)"\n' "$status_cmd"
	printf '\n'
	printf '# Message, mode and pane chrome.\n'
	printf 'set -g message-style "bg=%s,fg=%s"\n' "$pal_message_bg" "$pal_fg"
	printf 'set -g message-command-style "bg=%s,fg=%s"\n' "$pal_message_bg" "$pal_warn"
	printf 'set -g mode-style "bg=%s,fg=%s"\n' "$pal_mode_bg" "$pal_fg"
	printf 'set -g pane-border-style "fg=%s"\n' "$pal_border"
	printf 'set -g pane-active-border-style "fg=%s"\n' "$pal_active_border"
} >"$tmp_conf" || die "cannot write $tmp_conf"

chmod 644 "$tmp_state" "$tmp_conf" 2>/dev/null || true
mv -f "$tmp_state" "$STATE_FILE" || die "cannot install $STATE_FILE"
tmp_state=
mv -f "$tmp_conf" "$CONF_FILE" || die "cannot install $CONF_FILE"
tmp_conf=

exit 0
