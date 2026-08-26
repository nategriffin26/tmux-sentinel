"""ANSI & terminal renderer for live tmux-sentinel preview."""

import re
import shutil
from typing import Dict, Any, Tuple
from .themes import THEMES, GLYPH_SETS


def hex_to_rgb(hex_str: str) -> Tuple[int, int, int]:
    """Convert hex string '#rrggbb' to (r, g, b) tuple."""
    hex_clean = hex_str.lstrip("#")
    if len(hex_clean) == 3:
        hex_clean = "".join([c * 2 for c in hex_clean])
    if len(hex_clean) != 6:
        return (200, 200, 200)
    return (int(hex_clean[0:2], 16), int(hex_clean[2:4], 16), int(hex_clean[4:6], 16))


def tmux_tag_to_ansi(tag: str) -> str:
    """Convert a tmux #[...] style tag to ANSI escape sequence."""
    inner = tag[2:-1]  # strip #[ and ]
    codes = []
    for part in inner.split(","):
        part = part.strip()
        if not part:
            continue
        if part.startswith("fg="):
            val = part[3:]
            if val == "default":
                codes.append("\033[39m")
            elif val.startswith("#"):
                r, g, b = hex_to_rgb(val)
                codes.append(f"\033[38;2;{r};{g};{b}m")
        elif part.startswith("bg="):
            val = part[3:]
            if val == "default":
                codes.append("\033[49m")
            elif val.startswith("#"):
                r, g, b = hex_to_rgb(val)
                codes.append(f"\033[48;2;{r};{g};{b}m")
        elif part == "bold":
            codes.append("\033[1m")
        elif part in ("default", "none"):
            codes.append("\033[0m")
    return "".join(codes)


def tmux_to_ansi(tmux_str: str) -> str:
    """Convert a tmux-formatted string containing #[...] into ANSI."""
    # Replace #[...] with ANSI codes
    pattern = re.compile(r"#\[[^\]]*\]")
    return pattern.sub(lambda m: tmux_tag_to_ansi(m.group(0)), tmux_str) + "\033[0m"


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences to get printable character width."""
    ansi_escape = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    return ansi_escape.sub("", text)


def render_preview_bar(cfg: Dict[str, Any], width: int = 80, session_name: str = "main", sim_state: Dict[str, Any] = None) -> str:
    """Render a full simulated status bar line formatted with ANSI truecolor."""
    theme_name = cfg.get("theme", "catppuccin-mocha")
    theme = THEMES.get(theme_name, THEMES["catppuccin-mocha"])

    glyph_mode = cfg.get("glyph_mode", "nerd")
    glyphs = GLYPH_SETS.get(glyph_mode, GLYPH_SETS["nerd"])

    segments = cfg.get("segments", {})
    left_cfg = cfg.get("left", {})
    windows_cfg = cfg.get("windows", {})
    thresholds = cfg.get("thresholds", {})
    alerts_only = cfg.get("alerts_only", True)

    if sim_state is None:
        sim_state = {
            "thermal": 100,
            "sleep_risk": False,
            "disk_gb": 54,
            "batt_pct": 92,
            "batt_discharging": False,
            "cpu_pct": 24,
            "swap_gb": "23.3G",
            "pressure_level": 1,
            "multi_client": 1,
            "time_str": "14:28",
            "prefix_active": False,
            "in_copy_mode": False,
        }

    # Accent symbol & mode color
    accent_color = theme["accent"]
    if sim_state.get("prefix_active"):
        accent_color = theme["prefix"]
    elif sim_state.get("in_copy_mode"):
        accent_color = theme["copy_mode"]

    accent_symbol = left_cfg.get("accent_symbol", glyphs["accent"])
    max_len = left_cfg.get("max_session_length", 18)
    disp_session = session_name[:max_len]

    left_str = f"#[fg={accent_color}]{accent_symbol}#[fg={theme['fg']},bold] {disp_session} #[default]"

    # Window tabs (if not hidden)
    win_mode = windows_cfg.get("mode", "hidden")
    if win_mode == "minimal":
        left_str += f"#[fg={theme['accent']},bold] 1:main #[fg={theme['sep']}]|#[fg={theme['dim']}] 2:sh #[default]"
    elif win_mode == "tabs":
        left_str += f"#[fg={theme['bg']},bg={theme['accent']},bold] 1 #[fg={theme['fg']},bg={theme['mode_bg']}] main #[fg={theme['dim']},bg={theme['border']}] 2 #[fg={theme['fg']},bg={theme['message_bg']}] sh #[default]"

    # Right segments
    sep = f"#[fg={theme['sep']}]{glyphs['sep']}"
    right_parts = []

    # 1. Thermal
    if segments.get("thermal", True):
        t_val = sim_state.get("thermal", 100)
        if t_val < 100:
            right_parts.append(f"#[fg={theme['alert']}]{glyphs['thermal']} {t_val}%")
        elif not alerts_only:
            right_parts.append(f"#[fg={theme['dim']}]{glyphs['thermal']} #[fg={theme['val']}]{t_val}%")

    # 2. Sleep risk
    if segments.get("sleep_risk", True) and sim_state.get("sleep_risk", False):
        right_parts.append(f"#[fg={theme['alert']}]{glyphs['sleep']} 10m")

    # 3. Disk
    if segments.get("disk", True):
        d_val = sim_state.get("disk_gb", 54)
        c = theme["val"]
        if d_val < thresholds.get("disk_crit_gb", 15):
            c = theme["alert"]
        elif d_val < thresholds.get("disk_warn_gb", 25):
            c = theme["warn"]
        if not alerts_only or d_val < thresholds.get("disk_warn_gb", 25):
            right_parts.append(f"#[fg={theme['dim']}]{glyphs['disk']} #[fg={c}]{d_val}G")
        else:
            right_parts.append(f"#[fg={theme['dim']}]{glyphs['disk']} #[fg={theme['val']}]{d_val}G")

    # 4. Battery
    if segments.get("battery", True):
        b_pct = sim_state.get("batt_pct", 92)
        discharging = sim_state.get("batt_discharging", False)
        if discharging:
            c = theme["warn"]
            icon = glyphs["battery_full"]
            if b_pct < thresholds.get("battery_crit_pct", 20):
                c = theme["alert"]
                icon = glyphs["battery_low"]
            elif b_pct < thresholds.get("battery_warn_pct", 50):
                c = theme["peach"]
                icon = glyphs["battery_mid"]
            right_parts.append(f"#[fg={c}]{icon} {b_pct}%")
        elif not alerts_only:
            right_parts.append(f"#[fg={theme['dim']}]{glyphs['battery_full']} #[fg={theme['val']}]{b_pct}%")

    # 5. CPU
    if segments.get("cpu", True):
        cpu_val = sim_state.get("cpu_pct", 24)
        c = theme["val"]
        if cpu_val >= thresholds.get("cpu_crit_pct", 90):
            c = theme["alert"]
        elif cpu_val >= thresholds.get("cpu_warn_pct", 70):
            c = theme["peach"]
        right_parts.append(f"#[fg={theme['dim']}]{glyphs['cpu']} #[fg={c}]{cpu_val:2d}%")

    # 6. Memory / Swap
    if segments.get("memory", True):
        swap_str = sim_state.get("swap_gb", "23.3G")
        plvl = sim_state.get("pressure_level", 1)
        c = theme["val"]
        if plvl >= 4:
            c = theme["alert"]
        elif plvl >= 2:
            c = theme["warn"]
        right_parts.append(f"#[fg={theme['dim']}]{glyphs['memory']} #[fg={c}]{swap_str}")

    # 7. Multi-client
    if segments.get("multi_client", True) and sim_state.get("multi_client", 1) > 1:
        right_parts.append(f"#[fg={theme['info']}]{glyphs['clients']} {sim_state['multi_client']}")

    # 8. Clock
    if segments.get("clock", True):
        right_parts.append(f"#[fg={theme['fg']},bold]{sim_state.get('time_str', '14:28')} ")

    right_str = sep.join(right_parts)

    left_ansi = tmux_to_ansi(left_str)
    right_ansi = tmux_to_ansi(right_str)

    left_len = len(strip_ansi(left_ansi))
    right_len = len(strip_ansi(right_ansi))

    padding = width - left_len - right_len
    if padding < 1:
        padding = 1

    bg_style = "\033[49m"  # default transparent bg
    if theme["bg"] != "default":
        r, g, b = hex_to_rgb(theme["bg"])
        bg_style = f"\033[48;2;{r};{g};{b}m"

    return f"{bg_style}{left_ansi}{' ' * padding}{right_ansi}\033[0m"
