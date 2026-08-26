#!/bin/sh
# tmux-sentinel: status-right host segments -- single fork per status-interval.
# Philosophy: alerts-only. Healthy steady state shows load + swap; red/yellow
# segments (thermal, sleep-risk, low disk, battery discharging) inject
# themselves only when actionable, always with a quantitative level.

# 1. Load precomputed environment configuration
ENV_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/tmux-sentinel/env.sh"
if [ -f "$ENV_FILE" ]; then
    . "$ENV_FILE"
else
    # Default Catppuccin Mocha fallback
    SENTINEL_DIM='#[fg=#6c7086]'
    SENTINEL_VAL='#[fg=#a6adc8]'
    SENTINEL_SEP='#[fg=#45475a] · '
    SENTINEL_RED='#[fg=#f38ba8]'
    SENTINEL_YEL='#[fg=#f9e2af]'
    SENTINEL_PCH='#[fg=#fab387]'
    SENTINEL_INFO='#[fg=#94e2d5]'
    SENTINEL_GLYPH_THERMAL=''
    SENTINEL_GLYPH_SLEEP=''
    SENTINEL_GLYPH_DISK=''
    SENTINEL_GLYPH_BATT_FULL=''
    SENTINEL_GLYPH_BATT_MID=''
    SENTINEL_GLYPH_BATT_LOW=''
    SENTINEL_GLYPH_CPU=''
    SENTINEL_GLYPH_MEM='󰍛'
    SENTINEL_ALERTS_ONLY=1
    SENTINEL_SEG_THERMAL=1
    SENTINEL_SEG_SLEEP_RISK=1
    SENTINEL_SEG_DISK=1
    SENTINEL_SEG_BATTERY=1
    SENTINEL_SEG_CPU=1
    SENTINEL_SEG_MEMORY=1
    SENTINEL_DISK_WARN=25
    SENTINEL_DISK_CRIT=15
    SENTINEL_CPU_WARN=70
    SENTINEL_CPU_CRIT=90
    SENTINEL_BATT_WARN=50
    SENTINEL_BATT_CRIT=20
fi

out=''

# Detect OS
OS_NAME=$(uname -s 2>/dev/null || echo "Unknown")

# ==========================================
# 1. Thermal Throttling
# ==========================================
if [ "${SENTINEL_SEG_THERMAL:-1}" = "1" ]; then
    if [ "$OS_NAME" = "Darwin" ]; then
        lim=$(pmset -g therm 2>/dev/null | awk -F'= ' '/CPU_Speed_Limit/{print $2}')
        if [ -n "$lim" ] && [ "$lim" -lt 100 ] 2>/dev/null; then
            out="${out}${SENTINEL_RED}${SENTINEL_GLYPH_THERMAL} ${lim}%${SENTINEL_SEP}"
        elif [ "${SENTINEL_ALERTS_ONLY:-1}" = "0" ] && [ -n "$lim" ]; then
            out="${out}${SENTINEL_DIM}${SENTINEL_GLYPH_THERMAL} ${SENTINEL_VAL}${lim}%${SENTINEL_SEP}"
        fi
    elif [ "$OS_NAME" = "Linux" ]; then
        # Check Linux thermal zone if available
        if [ -d /sys/class/thermal/thermal_zone0 ]; then
            t_raw=$(cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null || echo 0)
            t_c=$((t_raw / 1000))
            if [ "$t_c" -ge 80 ] 2>/dev/null; then
                out="${out}${SENTINEL_RED}${SENTINEL_GLYPH_THERMAL} ${t_c}°C${SENTINEL_SEP}"
            elif [ "${SENTINEL_ALERTS_ONLY:-1}" = "0" ] && [ "$t_c" -gt 0 ]; then
                out="${out}${SENTINEL_DIM}${SENTINEL_GLYPH_THERMAL} ${SENTINEL_VAL}${t_c}°C${SENTINEL_SEP}"
            fi
        fi
    fi
fi

# ==========================================
# 2. Sleep Risk Alert (macOS / Linux)
# ==========================================
if [ "${SENTINEL_SEG_SLEEP_RISK:-1}" = "1" ]; then
    if [ "$OS_NAME" = "Darwin" ]; then
        slp=$(pmset -g 2>/dev/null | awk '/^ sleep /{print $2}')
        if [ -n "$slp" ] && [ "$slp" != "0" ]; then
            if ! pmset -g assertions 2>/dev/null | grep -Eq '(PreventUserIdleSystemSleep|PreventSystemSleep) +1'; then
                out="${out}${SENTINEL_RED}${SENTINEL_GLYPH_SLEEP} ${slp}m${SENTINEL_SEP}"
            fi
        fi
    fi
fi

# ==========================================
# 3. Disk Free
# ==========================================
if [ "${SENTINEL_SEG_DISK:-1}" = "1" ]; then
    avail=''
    if [ "$OS_NAME" = "Darwin" ]; then
        avail=$(df -g /System/Volumes/Data 2>/dev/null | awk 'NR==2{print $4}')
        if [ -z "$avail" ]; then
            avail=$(df -g / 2>/dev/null | awk 'NR==2{print $4}')
        fi
    else
        avail=$(df -k / 2>/dev/null | awk 'NR==2{print int($4/1048576)}')
    fi

    if [ -n "$avail" ]; then
        c=$SENTINEL_VAL
        [ "$avail" -lt "${SENTINEL_DISK_WARN:-25}" ] 2>/dev/null && c=$SENTINEL_YEL
        [ "$avail" -lt "${SENTINEL_DISK_CRIT:-15}" ] 2>/dev/null && c=$SENTINEL_RED

        if [ "${SENTINEL_ALERTS_ONLY:-1}" = "0" ] || [ "$avail" -lt "${SENTINEL_DISK_WARN:-25}" ] 2>/dev/null; then
            out="${out}${SENTINEL_DIM}${SENTINEL_GLYPH_DISK} ${c}${avail}G${SENTINEL_SEP}"
        else
            out="${out}${SENTINEL_DIM}${SENTINEL_GLYPH_DISK} ${SENTINEL_VAL}${avail}G${SENTINEL_SEP}"
        fi
    fi
fi

# ==========================================
# 4. Battery Status
# ==========================================
if [ "${SENTINEL_SEG_BATTERY:-1}" = "1" ]; then
    if [ "$OS_NAME" = "Darwin" ]; then
        batt=$(pmset -g batt 2>/dev/null)
        case $batt in
        *discharging*)
            pct=$(printf '%s' "$batt" | grep -Eo '[0-9]+%' | head -1 | tr -d '%')
            if [ -n "$pct" ]; then
                c=$SENTINEL_YEL
                i=$SENTINEL_GLYPH_BATT_FULL
                [ "$pct" -lt "${SENTINEL_BATT_WARN:-50}" ] 2>/dev/null && { c=$SENTINEL_PCH; i=$SENTINEL_GLYPH_BATT_MID; }
                [ "$pct" -lt "${SENTINEL_BATT_CRIT:-20}" ] 2>/dev/null && { c=$SENTINEL_RED; i=$SENTINEL_GLYPH_BATT_LOW; }
                out="${out}${c}${i} ${pct}%${SENTINEL_SEP}"
            fi
            ;;
        *)
            if [ "${SENTINEL_ALERTS_ONLY:-1}" = "0" ]; then
                pct=$(printf '%s' "$batt" | grep -Eo '[0-9]+%' | head -1 | tr -d '%')
                if [ -n "$pct" ]; then
                    out="${out}${SENTINEL_DIM}${SENTINEL_GLYPH_BATT_FULL} ${SENTINEL_VAL}${pct}%${SENTINEL_SEP}"
                fi
            fi
            ;;
        esac
    elif [ "$OS_NAME" = "Linux" ]; then
        if [ -d /sys/class/power_supply/BAT0 ] || [ -d /sys/class/power_supply/BAT1 ]; then
            bat_path="/sys/class/power_supply/BAT0"
            [ ! -d "$bat_path" ] && bat_path="/sys/class/power_supply/BAT1"
            status=$(cat "$bat_path/status" 2>/dev/null || echo "Unknown")
            pct=$(cat "$bat_path/capacity" 2>/dev/null || echo "")
            if [ "$status" = "Discharging" ] && [ -n "$pct" ]; then
                c=$SENTINEL_YEL
                i=$SENTINEL_GLYPH_BATT_FULL
                [ "$pct" -lt "${SENTINEL_BATT_WARN:-50}" ] 2>/dev/null && { c=$SENTINEL_PCH; i=$SENTINEL_GLYPH_BATT_MID; }
                [ "$pct" -lt "${SENTINEL_BATT_CRIT:-20}" ] 2>/dev/null && { c=$SENTINEL_RED; i=$SENTINEL_GLYPH_BATT_LOW; }
                out="${out}${c}${i} ${pct}%${SENTINEL_SEP}"
            elif [ "${SENTINEL_ALERTS_ONLY:-1}" = "0" ] && [ -n "$pct" ]; then
                out="${out}${SENTINEL_DIM}${SENTINEL_GLYPH_BATT_FULL} ${SENTINEL_VAL}${pct}%${SENTINEL_SEP}"
            fi
        fi
    fi
fi

# ==========================================
# 5. CPU Usage %
# ==========================================
if [ "${SENTINEL_SEG_CPU:-1}" = "1" ]; then
    cpu=''
    # Try C helper if present
    if [ -n "$SENTINEL_BIN_DIR" ] && [ -x "$SENTINEL_BIN_DIR/mac-cpu-pct" ]; then
        cpu=$("$SENTINEL_BIN_DIR/mac-cpu-pct" 2>/dev/null)
    elif [ -x "$HOME/.local/bin/mac-cpu-pct" ]; then
        cpu=$("$HOME/.local/bin/mac-cpu-pct" 2>/dev/null)
    elif command -v mac-cpu-pct >/dev/null 2>&1; then
        cpu=$(mac-cpu-pct 2>/dev/null)
    fi

    # Fallback to loadavg / ncpu
    if [ -z "$cpu" ]; then
        if [ "$OS_NAME" = "Darwin" ]; then
            cpu=$(sysctl -n vm.loadavg hw.ncpu 2>/dev/null | awk 'NR==1{sub(/^{ /,""); split($0,a," "); l=a[1]} NR==2{n=$1} END{if(n>0) print int((l/n)*100+0.5)}')
        else
            cpu=$(awk '{print $1}' /proc/loadavg 2>/dev/null | awk -v n="$(nproc 2>/dev/null || echo 1)" '{print int(($1/n)*100+0.5)}')
        fi
    fi

    c=$SENTINEL_VAL
    [ "${cpu:-0}" -ge "${SENTINEL_CPU_WARN:-70}" ] 2>/dev/null && c=$SENTINEL_PCH
    [ "${cpu:-0}" -ge "${SENTINEL_CPU_CRIT:-90}" ] 2>/dev/null && c=$SENTINEL_RED
    cpu_fmt=$(printf '%2d' "${cpu:-0}" 2>/dev/null || printf '%s' "${cpu:-0}")
    out="${out}${SENTINEL_DIM}${SENTINEL_GLYPH_CPU} ${c}${cpu_fmt}%${SENTINEL_SEP}"
fi

# ==========================================
# 6. Memory / Swap Usage & Pressure
# ==========================================
if [ "${SENTINEL_SEG_MEMORY:-1}" = "1" ]; then
    if [ "$OS_NAME" = "Darwin" ]; then
        swap=$(sysctl -n vm.swapusage 2>/dev/null | awk '{s=$6; sub(/M$/,"",s); printf "%.1fG", s/1024}')
        plvl=$(sysctl -n kern.memorystatus_vm_pressure_level 2>/dev/null)
        c=$SENTINEL_VAL
        [ "${plvl:-1}" -ge 2 ] 2>/dev/null && c=$SENTINEL_YEL
        [ "${plvl:-1}" -ge 4 ] 2>/dev/null && c=$SENTINEL_RED
        out="${out}${SENTINEL_DIM}${SENTINEL_GLYPH_MEM} ${c}${swap}${SENTINEL_SEP}"
    elif [ "$OS_NAME" = "Linux" ]; then
        swap_used=$(awk '/SwapTotal/{t=$2} /SwapFree/{f=$2} END{if(t>0) printf "%.1fG", (t-f)/1048576; else print "0.0G"}' /proc/meminfo 2>/dev/null)
        mem_avail=$(awk '/MemAvailable/{a=$2} /MemTotal/{t=$2} END{if(t>0) print int((a/t)*100)}' /proc/meminfo 2>/dev/null)
        c=$SENTINEL_VAL
        [ "${mem_avail:-100}" -le 20 ] 2>/dev/null && c=$SENTINEL_YEL
        [ "${mem_avail:-100}" -le 10 ] 2>/dev/null && c=$SENTINEL_RED
        out="${out}${SENTINEL_DIM}${SENTINEL_GLYPH_MEM} ${c}${swap_used}${SENTINEL_SEP}"
    fi
fi

printf '%s' "$out"
