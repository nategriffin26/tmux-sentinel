#!/bin/sh
# ==============================================================================
# tmux-sentinel — re-source the generated config into a running tmux server.
#
# Three distinct outcomes, none of them conflated:
#   exit 0  reloaded, and it really did reload (tmux exited 0)
#   exit 0  no tmux server is running, so there was nothing to reload
#   exit 1  tmux was there and refused; its own stderr is passed through
#
# v1 printed "reloaded successfully" unconditionally because every tmux call
# ended in `|| true`. Exit status is checked here.
# ==============================================================================

set -u

PROG=tmux-sentinel
CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/tmux-sentinel"
CONF_FILE=$CONFIG_DIR/sentinel.conf

tmux_cmd() {
	if [ -n "${TMUX_SENTINEL_SOCKET:-}" ]; then
		tmux -L "$TMUX_SENTINEL_SOCKET" "$@"
	else
		tmux "$@"
	fi
}

if ! command -v tmux >/dev/null 2>&1; then
	printf '%s: tmux is not on PATH.\n' "$PROG" >&2
	exit 1
fi

if [ ! -f "$CONF_FILE" ]; then
	printf '%s: %s does not exist; run the plugin entrypoint or "make install" first.\n' \
		"$PROG" "$CONF_FILE" >&2
	exit 1
fi

# `show-options -g` never spawns a server, so this probe cannot create the very
# thing it tests for. A nonzero exit alone does not mean "no server": a broken
# or wrapped tmux also exits nonzero, and reporting that as "nothing to reload"
# is exactly the lie v1 told. Classify by what tmux actually said.
probe_err=$(tmux_cmd show-options -g 2>&1 >/dev/null) && probe_rc=0 || probe_rc=$?
if [ "$probe_rc" -ne 0 ]; then
	case $probe_err in
	*'no server running'* | *'error connecting'*)
		printf '%s: no tmux server is running; nothing to reload.\n' "$PROG"
		exit 0
		;;
	*)
		printf '%s: tmux failed: %s\n' "$PROG" "$probe_err" >&2
		exit 1
		;;
	esac
fi

if ! tmux_cmd source-file "$CONF_FILE"; then
	printf '%s: tmux refused %s; the status bar is unchanged.\n' "$PROG" "$CONF_FILE" >&2
	exit 1
fi

# A server with no attached client cannot be refreshed and does not need to be:
# the config is loaded and the next client to attach draws the new bar.
if [ -n "$(tmux_cmd list-clients -F '#{client_name}' 2>/dev/null)" ]; then
	if ! tmux_cmd refresh-client -S; then
		printf '%s: config loaded but refresh-client failed; the bar updates on the next tick.\n' \
			"$PROG" >&2
		exit 1
	fi
fi

printf '%s: reloaded %s.\n' "$PROG" "$CONF_FILE"
