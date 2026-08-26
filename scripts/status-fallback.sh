#!/bin/sh
# ==============================================================================
# tmux-sentinel — reduced-mode status renderer.
#
# Used only while bin/sentinel-status has not been built. Renders four of the
# eight segments: disk, cpu, memory and clock. The cpu figure here is a load
# average, not a utilisation percentage, and is shown as such (no `%`) so the
# bar never claims precision it does not have. The memory figure IS the same
# quantity the binary reports — percent of physical memory unavailable — so
# building the binary does not change what that number means. Once `make`
# produces the binary, scripts/generate.sh re-points status-right at it
# automatically.
#
# Fork budget: 3 on Darwin (df, sysctl, date), 2 on Linux (df, date); /proc and
# sentinel.state are read with shell builtins.
#
# sentinel.state is PARSED, never sourced. Sourcing it would hand every status
# tick to whatever the file happens to contain.
# ==============================================================================

set -u

NL=$(printf '\nx')
NL=${NL%x}

# Reader defaults, per the state-file contract: a missing or partial state file
# must never stop the bar from rendering.
always_disk=1
clock_format=%H:%M
color_fg='#cdd6f4'
color_dim='#6c7086'
color_val='#a6adc8'
color_sep='#45475a'
color_warn='#f9e2af'
color_alert='#f38ba8'
seg_disk=1
seg_cpu=1
seg_memory=1
seg_clock=1
glyph_sep=' · '
glyph_disk='DISK'
glyph_cpu='CPU'
glyph_memory='MEM'
disk_warn_gb=25
disk_crit_gb=15
cpu_warn_pct=70
cpu_crit_pct=90
memory_warn_pct=80
memory_crit_pct=90

STATE="${XDG_CONFIG_HOME:-$HOME/.config}/tmux-sentinel/sentinel.state"

if [ -r "$STATE" ]; then
	while IFS='=' read -r key value; do
		case $key in
		'' | '#'*) continue ;;
		esac
		case $key in
		always_disk) always_disk=$value ;;
		clock_format) clock_format=$value ;;
		color_fg) color_fg=$value ;;
		color_dim) color_dim=$value ;;
		color_val) color_val=$value ;;
		color_sep) color_sep=$value ;;
		color_warn) color_warn=$value ;;
		color_alert) color_alert=$value ;;
		seg_disk) seg_disk=$value ;;
		seg_cpu) seg_cpu=$value ;;
		seg_memory) seg_memory=$value ;;
		seg_clock) seg_clock=$value ;;
		glyph_sep) glyph_sep=$value ;;
		glyph_disk) glyph_disk=$value ;;
		glyph_cpu) glyph_cpu=$value ;;
		glyph_memory) glyph_memory=$value ;;
		disk_warn_gb) disk_warn_gb=$value ;;
		disk_crit_gb) disk_crit_gb=$value ;;
		cpu_warn_pct) cpu_warn_pct=$value ;;
		cpu_crit_pct) cpu_crit_pct=$value ;;
		memory_warn_pct) memory_warn_pct=$value ;;
		memory_crit_pct) memory_crit_pct=$value ;;
		esac
	done <"$STATE"
fi

# Anything non-numeric in the state file falls back to the built-in default
# rather than poisoning the arithmetic below.
_v=
for _n in always_disk disk_warn_gb disk_crit_gb cpu_warn_pct cpu_crit_pct \
	memory_warn_pct memory_crit_pct seg_disk seg_cpu seg_memory seg_clock; do
	eval "_v=\$$_n"
	case $_v in
	'' | *[!0-9]*) eval "$_n=0" ;;
	esac
done

# ---------------------------------------------------------------- measurements
disk_gb=
df_out=$(df -Pk / 2>/dev/null) || df_out=
if [ -n "$df_out" ]; then
	df_row=${df_out#*"$NL"}
	df_row=${df_row%%"$NL"*}
	# shellcheck disable=SC2086 # deliberate word splitting of df columns
	set -- $df_row
	if [ $# -ge 4 ]; then
		case $4 in
		'' | *[!0-9]*) ;;
		*) disk_gb=$((${4} / 1048576)) ;;
		esac
	fi
fi

load_txt=
load_hundredths=0
ncpu=1
mem_pct=
mem_pressure=0

if [ -r /proc/loadavg ]; then
	while read -r l1 _rest; do
		load_txt=$l1
		break
	done </proc/loadavg
	ncpu=0
	while IFS= read -r line; do
		case $line in
		processor*) ncpu=$((ncpu + 1)) ;;
		esac
	done </proc/cpuinfo 2>/dev/null || ncpu=0
	[ "$ncpu" -ge 1 ] || ncpu=1
	# Percent of physical memory unavailable to a new allocation — the Linux
	# spelling of Darwin's kern.memorystatus_level, inverted.
	mem_total=0
	mem_avail=0
	while read -r mkey mval _munit; do
		case $mkey in
		MemTotal:) mem_total=$mval ;;
		MemAvailable:) mem_avail=$mval ;;
		esac
	done </proc/meminfo 2>/dev/null
	case $mem_total$mem_avail in
	'' | *[!0-9]*) ;;
	*)
		if [ "$mem_total" -gt 0 ] && [ "$mem_avail" -le "$mem_total" ]; then
			mem_pct=$((100 - (mem_avail * 100 + mem_total / 2) / mem_total))
		fi
		;;
	esac
else
	sysctl_out=$(sysctl -n vm.loadavg hw.ncpu kern.memorystatus_level kern.memorystatus_vm_pressure_level 2>/dev/null) || sysctl_out=
	if [ -n "$sysctl_out" ]; then
		sc_line1=${sysctl_out%%"$NL"*}
		sc_rest=${sysctl_out#*"$NL"}
		sc_line2=${sc_rest%%"$NL"*}
		sc_rest=${sc_rest#*"$NL"}
		sc_line3=${sc_rest%%"$NL"*}
		sc_line4=${sc_rest#*"$NL"}
		sc_line4=${sc_line4%%"$NL"*}

		# vm.loadavg reads `{ 1.23 1.10 0.98 }`.
		# shellcheck disable=SC2086 # deliberate word splitting
		set -- $sc_line1
		if [ $# -ge 2 ]; then
			load_txt=$2
		fi
		case $sc_line2 in
		'' | *[!0-9]*) ;;
		*) ncpu=$sc_line2 ;;
		esac
		[ "$ncpu" -ge 1 ] || ncpu=1

		# kern.memorystatus_level is the kernel's own percent-available
		# figure, the one memory_pressure(1) prints; report its inverse.
		case $sc_line3 in
		'' | *[!0-9]*) ;;
		*)
			if [ "$sc_line3" -le 100 ]; then
				mem_pct=$((100 - sc_line3))
			fi
			;;
		esac
		case $sc_line4 in
		'' | *[!0-9]*) ;;
		*) mem_pressure=$sc_line4 ;;
		esac
	fi
fi

# A load average of 2.53 on 8 cores is 253/8 = 31%. Kept in hundredths so the
# whole calculation stays in integer arithmetic.
case $load_txt in
'' | *[!0-9.]*) load_txt= ;;
*)
	_int=${load_txt%%.*}
	_frac=${load_txt#*.}
	case $load_txt in
	*.*) ;;
	*) _frac=0 ;;
	esac
	_frac=${_frac}00
	_frac=${_frac%"${_frac#??}"}
	case $_int$_frac in
	'' | *[!0-9]*) load_txt= ;;
	*) load_hundredths=$((_int * 100 + _frac)) ;;
	esac
	;;
esac
cpu_pct=$((load_hundredths / ncpu))
[ "$cpu_pct" -le 100 ] || cpu_pct=100

# -------------------------------------------------------------------- rendering
out=
sep="#[fg=$color_sep]$glyph_sep"

# The separator goes between rendered segments only: never leading, never
# trailing. Appending it after every segment is the v1 bug being fixed here.
add() {
	if [ -n "$out" ]; then
		out=$out$sep$1
	else
		out=$1
	fi
}

if [ "$seg_disk" = 1 ] && [ -n "$disk_gb" ]; then
	if [ "$always_disk" = 1 ] || [ "$disk_gb" -lt "$disk_warn_gb" ]; then
		colour=$color_val
		if [ "$disk_gb" -lt "$disk_warn_gb" ]; then colour=$color_warn; fi
		if [ "$disk_gb" -lt "$disk_crit_gb" ]; then colour=$color_alert; fi
		add "#[fg=$color_dim]$glyph_disk #[fg=$colour]${disk_gb}G"
	fi
fi

if [ "$seg_cpu" = 1 ] && [ -n "$load_txt" ]; then
	colour=$color_val
	if [ "$cpu_pct" -ge "$cpu_warn_pct" ]; then colour=$color_warn; fi
	if [ "$cpu_pct" -ge "$cpu_crit_pct" ]; then colour=$color_alert; fi
	# Spelled `load 4.83`, never a bare `4.83`, because the native engine puts
	# a utilisation percentage in this slot. Without the word, building the
	# binary would silently change what the same number means.
	add "#[fg=$color_dim]$glyph_cpu #[fg=$colour]load $load_txt"
fi

if [ "$seg_memory" = 1 ] && [ -n "$mem_pct" ]; then
	colour=$color_val
	if [ "$mem_pct" -ge "$memory_warn_pct" ] || [ "$mem_pressure" -ge 2 ]; then colour=$color_warn; fi
	if [ "$mem_pct" -ge "$memory_crit_pct" ] || [ "$mem_pressure" -ge 4 ]; then colour=$color_alert; fi
	add "#[fg=$color_dim]$glyph_memory #[fg=$colour]$mem_pct%"
fi

if [ "$seg_clock" = 1 ]; then
	now=$(date +"$clock_format" 2>/dev/null) || now=
	if [ -n "$now" ]; then
		add "#[fg=$color_fg,bold]$now"
	fi
fi

printf '%s' "$out"
