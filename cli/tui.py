"""Interactive Curses TUI Customizer with real-time status bar preview."""

import curses
import os
import subprocess
import time
from typing import Dict, Any, List

from .config import load_config, save_config
from .themes import THEMES, GLYPH_SETS
from .generator import generate_all
from .renderer import hex_to_rgb


class SentinelTUI:
    def __init__(self, stdscr: curses.window):
        self.stdscr = stdscr
        self.cfg = load_config()
        self.selected_category = 0
        self.selected_subitem = 0
        self.active_pane = 0  # 0: Category list, 1: Options list
        self.sim_alerts = False
        self.message = ""
        self.message_time = 0.0

        # Setup curses
        curses.curs_set(0)
        curses.use_default_colors()
        self.stdscr.timeout(100)  # non-blocking with 100ms refresh

        # Setup color pairs
        self._init_colors()

    def _init_colors(self):
        try:
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(1, curses.COLOR_CYAN, -1)     # Accent / Header
            curses.init_pair(2, curses.COLOR_GREEN, -1)    # Success / Active
            curses.init_pair(3, curses.COLOR_YELLOW, -1)   # Warning
            curses.init_pair(4, curses.COLOR_RED, -1)      # Alert
            curses.init_pair(5, curses.COLOR_WHITE, curses.COLOR_BLUE)  # Highlighted
            curses.init_pair(6, curses.COLOR_MAGENTA, -1)  # Special
            curses.init_pair(7, curses.COLOR_BLACK, curses.COLOR_CYAN)  # Selected Tag
            curses.init_pair(8, curses.COLOR_WHITE, -1)    # Bright White
        except Exception:
            pass

    def run(self):
        while True:
            self._draw()
            try:
                ch = self.stdscr.getch()
            except Exception:
                ch = -1

            if ch == -1:
                continue

            if ch in (ord('q'), ord('Q'), 27):  # ESC or q
                break
            elif ch in (curses.KEY_RESIZE,):
                self.stdscr.clear()
            elif ch in (ord('s'), ord('S')):
                self._save_and_reload()
            elif ch in (ord('t'), ord('T')):
                self._cycle_theme()
            elif ch in (ord('a'), ord('A')):
                self.sim_alerts = not self.sim_alerts
                self._set_msg(f"Alert simulation: {'ON (Warning State)' if self.sim_alerts else 'OFF (Healthy State)'}")
            elif ch in (curses.KEY_UP, ord('k')):
                self._handle_up()
            elif ch in (curses.KEY_DOWN, ord('j')):
                self._handle_down()
            elif ch in (curses.KEY_LEFT, ord('h')):
                self.active_pane = 0
            elif ch in (curses.KEY_RIGHT, ord('l'), ord('\t')):
                self.active_pane = 1
            elif ch in (ord('\n'), ord('\r'), ord(' ')):
                self._handle_select()

    def _set_msg(self, msg: str):
        self.message = msg
        self.message_time = time.time()

    def _cycle_theme(self):
        theme_keys = list(THEMES.keys())
        curr = self.cfg.get("theme", "catppuccin-mocha")
        idx = theme_keys.index(curr) if curr in theme_keys else 0
        nxt = theme_keys[(idx + 1) % len(theme_keys)]
        self.cfg["theme"] = nxt
        self._set_msg(f"Theme switched to: {THEMES[nxt]['name']}")

    def _save_and_reload(self):
        save_config(self.cfg)
        generate_all(self.cfg)
        # Reload tmux if running
        try:
            conf_file = os.path.expanduser("~/.config/tmux-sentinel/sentinel.conf")
            subprocess.run(["tmux", "source-file", conf_file], capture_output=True)
            subprocess.run(["tmux", "refresh-client", "-S"], capture_output=True)
            self._set_msg("✓ Configuration saved & tmux reloaded live!")
        except Exception:
            self._set_msg("✓ Configuration saved.")

    def _categories(self) -> List[str]:
        return [
            "🎨 Color Theme",
            "🧩 Health Segments",
            "⚡ Alerts-Only Mode",
            "🔤 Glyph Style",
            "📐 Bar Position & Layout",
            "🗂️ Window Tabs Style",
            "🧪 Test Alert Simulation",
            "💾 Save & Apply to Tmux",
        ]

    def _handle_up(self):
        if self.active_pane == 0:
            self.selected_category = (self.selected_category - 1) % len(self._categories())
            self.selected_subitem = 0
        else:
            sub_count = self._get_subitem_count()
            if sub_count > 0:
                self.selected_subitem = (self.selected_subitem - 1) % sub_count

    def _handle_down(self):
        if self.active_pane == 0:
            self.selected_category = (self.selected_category + 1) % len(self._categories())
            self.selected_subitem = 0
        else:
            sub_count = self._get_subitem_count()
            if sub_count > 0:
                self.selected_subitem = (self.selected_subitem + 1) % sub_count

    def _get_subitem_count(self) -> int:
        cat = self.selected_category
        if cat == 0:  # Theme
            return len(THEMES)
        elif cat == 1:  # Segments
            return len(self.cfg.get("segments", {}))
        elif cat == 2:  # Alerts-only
            return 2
        elif cat == 3:  # Glyphs
            return len(GLYPH_SETS)
        elif cat == 4:  # Position
            return 3
        elif cat == 5:  # Window Tabs
            return 3
        elif cat == 6:  # Sim
            return 2
        elif cat == 7:  # Save
            return 1
        return 0

    def _handle_select(self):
        cat = self.selected_category
        if self.active_pane == 0 and cat != 7:
            self.active_pane = 1
            return

        if cat == 0:  # Theme selection
            theme_keys = list(THEMES.keys())
            if 0 <= self.selected_subitem < len(theme_keys):
                k = theme_keys[self.selected_subitem]
                self.cfg["theme"] = k
                self._set_msg(f"Theme set to: {THEMES[k]['name']}")
        elif cat == 1:  # Segments toggle
            seg_keys = list(self.cfg.get("segments", {}).keys())
            if 0 <= self.selected_subitem < len(seg_keys):
                k = seg_keys[self.selected_subitem]
                curr = self.cfg["segments"].get(k, True)
                self.cfg["segments"][k] = not curr
                self._set_msg(f"Segment '{k}': {'ENABLED' if not curr else 'DISABLED'}")
        elif cat == 2:  # Alerts-only toggle
            self.cfg["alerts_only"] = (self.selected_subitem == 0)
            self._set_msg(f"Mode: {'Alerts-Only (Quiet steady state)' if self.cfg['alerts_only'] else 'Always-On (All metrics visible)'}")
        elif cat == 3:  # Glyphs
            glyph_keys = list(GLYPH_SETS.keys())
            if 0 <= self.selected_subitem < len(glyph_keys):
                k = glyph_keys[self.selected_subitem]
                self.cfg["glyph_mode"] = k
                self._set_msg(f"Glyphs: {GLYPH_SETS[k]['name']}")
        elif cat == 4:  # Position
            pos_options = ["top", "bottom"]
            if self.selected_subitem < 2:
                self.cfg["position"] = pos_options[self.selected_subitem]
                self._set_msg(f"Position: {self.cfg['position']}")
            elif self.selected_subitem == 2:
                accents = ["▌", "█", "◆", "|", "■"]
                curr = self.cfg.get("left", {}).get("accent_symbol", "▌")
                nxt = accents[(accents.index(curr) + 1) % len(accents)] if curr in accents else "▌"
                self.cfg["left"]["accent_symbol"] = nxt
                self._set_msg(f"Left Accent: {nxt}")
        elif cat == 5:  # Window tabs
            modes = ["hidden", "minimal", "tabs"]
            if self.selected_subitem < len(modes):
                self.cfg["windows"]["mode"] = modes[self.selected_subitem]
                self._set_msg(f"Window Tabs: {modes[self.selected_subitem]}")
        elif cat == 6:  # Sim
            self.sim_alerts = (self.selected_subitem == 1)
            self._set_msg(f"Simulation: {'Warning/Critical Alerts' if self.sim_alerts else 'Normal Healthy State'}")
        elif cat == 7:  # Save
            self._save_and_reload()

    def _draw(self):
        self.stdscr.erase()
        h, w = self.stdscr.getmaxyx()
        if h < 20 or w < 60:
            self.stdscr.addstr(0, 0, f"Terminal too small ({w}x{h}). Resize to at least 80x24.")
            self.stdscr.refresh()
            return

        # 1. Title Banner
        title = " 󰌘 TMUX-SENTINEL CUSTOMIZER "
        self.stdscr.addstr(0, 2, title, curses.A_BOLD | curses.color_pair(1))
        info_str = "Press [s] to Save & Reload | [q] to Exit"
        if w > len(title) + len(info_str) + 4:
            self.stdscr.addstr(0, w - len(info_str) - 2, info_str, curses.color_pair(3))

        # 2. Live Preview Box
        self._draw_preview_box(2, 2, w - 4)

        # 3. Message Bar (if recent)
        msg_y = 7
        if self.message and (time.time() - self.message_time < 4.0):
            self.stdscr.addstr(msg_y, 4, f"● {self.message}", curses.A_BOLD | curses.color_pair(2))
        else:
            self.stdscr.addstr(msg_y, 4, "─" * (w - 8), curses.color_pair(1))

        # 4. Two-Column Layout (Categories & Options)
        body_y = 9
        body_h = h - body_y - 3
        col_w = 26

        self._draw_categories(body_y, 2, col_w, body_h)
        self._draw_options(body_y, col_w + 5, w - col_w - 7, body_h)

        # 5. Bottom Help Bar
        help_text = "[↑/↓/j/k] Navigate  [Enter/Space] Select  [Tab/←/→] Switch Pane  [t] Theme  [a] Sim Alert  [s] Save"
        self.stdscr.addstr(h - 2, 2, help_text[:w-4], curses.color_pair(1))

        self.stdscr.refresh()

    def _draw_preview_box(self, y: int, x: int, width: int):
        # Draw border box
        self.stdscr.addstr(y, x, "╭─ Live Status Bar Preview " + "─" * (width - 27) + "╮", curses.color_pair(1))
        self.stdscr.addstr(y + 1, x, "│", curses.color_pair(1))
        self.stdscr.addstr(y + 1, x + width - 1, "│", curses.color_pair(1))
        self.stdscr.addstr(y + 2, x, "│", curses.color_pair(1))
        self.stdscr.addstr(y + 2, x + width - 1, "│", curses.color_pair(1))
        self.stdscr.addstr(y + 3, x, "╰" + "─" * (width - 2) + "╯", curses.color_pair(1))

        # Simulated state
        if self.sim_alerts:
            sim_state = {
                "thermal": 82,
                "sleep_risk": True,
                "disk_gb": 12,
                "batt_pct": 18,
                "batt_discharging": True,
                "cpu_pct": 94,
                "swap_gb": "24.1G",
                "pressure_level": 4,
                "multi_client": 2,
                "time_str": "15:42",
                "prefix_active": False,
                "in_copy_mode": False,
            }
        else:
            sim_state = {
                "thermal": 100,
                "sleep_risk": False,
                "disk_gb": 54,
                "batt_pct": 95,
                "batt_discharging": False,
                "cpu_pct": 22,
                "swap_gb": "23.3G",
                "pressure_level": 1,
                "multi_client": 1,
                "time_str": "15:42",
                "prefix_active": False,
                "in_copy_mode": False,
            }

        # Build preview string
        theme_name = self.cfg.get("theme", "catppuccin-mocha")
        theme = THEMES.get(theme_name, THEMES["catppuccin-mocha"])
        glyphs = GLYPH_SETS.get(self.cfg.get("glyph_mode", "nerd"), GLYPH_SETS["nerd"])
        accent_sym = self.cfg.get("left", {}).get("accent_symbol", glyphs["accent"])
        win_mode = self.cfg.get("windows", {}).get("mode", "hidden")

        left_str = f" {accent_sym}  main "
        if win_mode == "minimal":
            left_str += "| 1:dev | 2:sh "
        elif win_mode == "tabs":
            left_str += "[1 dev] [2 sh] "

        # Right segments
        sep = glyphs["sep"]
        r_parts = []
        segs = self.cfg.get("segments", {})
        alerts_only = self.cfg.get("alerts_only", True)

        if segs.get("thermal", True):
            if sim_state["thermal"] < 100:
                r_parts.append(f"{glyphs['thermal']} {sim_state['thermal']}% (WARN)")
            elif not alerts_only:
                r_parts.append(f"{glyphs['thermal']} 100%")

        if segs.get("sleep_risk", True) and sim_state["sleep_risk"]:
            r_parts.append(f"{glyphs['sleep']} 10m (RISK)")

        if segs.get("disk", True):
            r_parts.append(f"{glyphs['disk']} {sim_state['disk_gb']}G")

        if segs.get("battery", True):
            if sim_state["batt_discharging"]:
                r_parts.append(f"{glyphs['battery_low']} {sim_state['batt_pct']}%")
            elif not alerts_only:
                r_parts.append(f"{glyphs['battery_full']} {sim_state['batt_pct']}%")

        if segs.get("cpu", True):
            r_parts.append(f"{glyphs['cpu']} {sim_state['cpu_pct']}%")

        if segs.get("memory", True):
            r_parts.append(f"{glyphs['memory']} {sim_state['swap_gb']}")

        if segs.get("multi_client", True) and sim_state["multi_client"] > 1:
            r_parts.append(f"{glyphs['clients']} {sim_state['multi_client']}")

        if segs.get("clock", True):
            r_parts.append(f"{sim_state['time_str']} ")

        right_str = sep.join(r_parts)
        inner_w = width - 4
        pad = inner_w - len(left_str) - len(right_str)
        if pad < 1:
            pad = 1

        bar_line = (left_str + " " * pad + right_str)[:inner_w]

        # Draw simulated preview bar
        self.stdscr.addstr(y + 1, x + 2, bar_line, curses.A_REVERSE | curses.color_pair(1))
        sim_label = f"[{'⚠ Simulating Alert State' if self.sim_alerts else '✔ Simulating Healthy State'}] Theme: {theme['name']} (pos: {self.cfg.get('position', 'top')})"
        self.stdscr.addstr(y + 2, x + 2, sim_label[:inner_w], curses.color_pair(3 if self.sim_alerts else 2))

    def _draw_categories(self, y: int, x: int, width: int, height: int):
        cats = self._categories()
        for idx, cat in enumerate(cats):
            if idx >= height:
                break
            is_sel = (idx == self.selected_category)
            is_active = (is_sel and self.active_pane == 0)

            prefix = "▶ " if is_sel else "  "
            line = f"{prefix}{cat}"[:width].ljust(width)

            if is_active:
                self.stdscr.addstr(y + idx, x, line, curses.A_BOLD | curses.color_pair(5))
            elif is_sel:
                self.stdscr.addstr(y + idx, x, line, curses.A_BOLD | curses.color_pair(1))
            else:
                self.stdscr.addstr(y + idx, x, line, curses.color_pair(8))

    def _draw_options(self, y: int, x: int, width: int, height: int):
        cat = self.selected_category

        if cat == 0:  # Theme
            theme_keys = list(THEMES.keys())
            curr_theme = self.cfg.get("theme", "catppuccin-mocha")
            for idx, k in enumerate(theme_keys):
                if idx >= height:
                    break
                t = THEMES[k]
                is_curr = (k == curr_theme)
                is_sel = (idx == self.selected_subitem and self.active_pane == 1)

                marker = "● " if is_curr else "○ "
                text = f"{marker}{t['name']} - {t['description']}"[:width]
                if is_sel:
                    self.stdscr.addstr(y + idx, x, text.ljust(width), curses.A_BOLD | curses.color_pair(5))
                elif is_curr:
                    self.stdscr.addstr(y + idx, x, text, curses.A_BOLD | curses.color_pair(2))
                else:
                    self.stdscr.addstr(y + idx, x, text, curses.color_pair(8))

        elif cat == 1:  # Segments
            segs = self.cfg.get("segments", {})
            seg_items = [
                ("thermal", "Thermal Throttling", "Alerts in red when CPU speed limit < 100%"),
                ("sleep_risk", "Sleep Risk Watchdog", "Alerts when idle sleep armed without wake assertion"),
                ("disk", "Disk Free Space", "Quantified disk GB (yellow <25G, red <15G)"),
                ("battery", "Battery Monitor", "Shows discharge level; alert colored on low charge"),
                ("cpu", "CPU Usage %", "Calculates delta CPU ticks; alerts on high load"),
                ("memory", "Memory Pressure & Swap", "Shows swap used, colored by kernel memory pressure"),
                ("multi_client", "Multi-Client Indicator", "Shows icon & connected count when > 1 client attached"),
                ("clock", "Clock", "Displays time/date in status bar"),
            ]
            for idx, (key, name, desc) in enumerate(seg_items):
                if idx >= height:
                    break
                enabled = segs.get(key, True)
                is_sel = (idx == self.selected_subitem and self.active_pane == 1)

                status = "[ENABLED] " if enabled else "[DISABLED]"
                text = f"{status} {name} ── {desc}"[:width]

                if is_sel:
                    self.stdscr.addstr(y + idx, x, text.ljust(width), curses.A_BOLD | curses.color_pair(5))
                elif enabled:
                    self.stdscr.addstr(y + idx, x, text, curses.A_BOLD | curses.color_pair(2))
                else:
                    self.stdscr.addstr(y + idx, x, text, curses.color_pair(4))

        elif cat == 2:  # Alerts-only mode
            options = [
                ("Alerts-Only (Recommended)", "Whisper-quiet steady state; segments appear only when actionable/risky."),
                ("Always-On Metrics", "Display all enabled health metrics continuously in status bar."),
            ]
            curr_alerts = self.cfg.get("alerts_only", True)
            for idx, (name, desc) in enumerate(options):
                is_curr = (idx == 0 and curr_alerts) or (idx == 1 and not curr_alerts)
                is_sel = (idx == self.selected_subitem and self.active_pane == 1)

                marker = "● " if is_curr else "○ "
                text = f"{marker}{name}\n     {desc}"
                line1 = f"{marker}{name}"
                line2 = f"    {desc}"

                row = y + (idx * 3)
                if row + 1 < y + height:
                    if is_sel:
                        self.stdscr.addstr(row, x, line1.ljust(width), curses.A_BOLD | curses.color_pair(5))
                    elif is_curr:
                        self.stdscr.addstr(row, x, line1, curses.A_BOLD | curses.color_pair(2))
                    else:
                        self.stdscr.addstr(row, x, line1, curses.color_pair(8))
                    self.stdscr.addstr(row + 1, x, line2[:width], curses.color_pair(1))

        elif cat == 3:  # Glyphs
            glyph_keys = list(GLYPH_SETS.keys())
            curr_glyph = self.cfg.get("glyph_mode", "nerd")
            for idx, k in enumerate(glyph_keys):
                g = GLYPH_SETS[k]
                is_curr = (k == curr_glyph)
                is_sel = (idx == self.selected_subitem and self.active_pane == 1)

                sample = f"{g['accent']} {g['thermal']} {g['sleep']} {g['disk']} {g['battery_full']} {g['cpu']} {g['memory']} {g['clients']}"
                line = f"{'●' if is_curr else '○'} {g['name']} ── Example: {sample}"[:width]

                if is_sel:
                    self.stdscr.addstr(y + idx, x, line.ljust(width), curses.A_BOLD | curses.color_pair(5))
                elif is_curr:
                    self.stdscr.addstr(y + idx, x, line, curses.A_BOLD | curses.color_pair(2))
                else:
                    self.stdscr.addstr(y + idx, x, line, curses.color_pair(8))

        elif cat == 4:  # Position & Layout
            options = [
                ("Top Position", "Puts status bar at top (preserves bottom of pane for agent CLI / prompt)"),
                ("Bottom Position", "Standard traditional tmux bottom status bar"),
                (f"Left Accent Symbol: {self.cfg.get('left', {}).get('accent_symbol', '▌')}", "Cycle accent glyph indicator (▌, █, ◆, |, ■)"),
            ]
            curr_pos = self.cfg.get("position", "top")
            for idx, (name, desc) in enumerate(options):
                is_curr = (idx == 0 and curr_pos == "top") or (idx == 1 and curr_pos == "bottom")
                is_sel = (idx == self.selected_subitem and self.active_pane == 1)

                marker = "● " if is_curr else "○ " if idx < 2 else "▶ "
                line1 = f"{marker}{name}"
                line2 = f"    {desc}"

                row = y + (idx * 3)
                if row + 1 < y + height:
                    if is_sel:
                        self.stdscr.addstr(row, x, line1.ljust(width), curses.A_BOLD | curses.color_pair(5))
                    elif is_curr:
                        self.stdscr.addstr(row, x, line1, curses.A_BOLD | curses.color_pair(2))
                    else:
                        self.stdscr.addstr(row, x, line1, curses.color_pair(8))
                    self.stdscr.addstr(row + 1, x, line2[:width], curses.color_pair(1))

        elif cat == 5:  # Window Tabs
            options = [
                ("Hidden / Zen Mode (Recommended)", "Zero tab clutter. Perfect for one-task/one-agent per session."),
                ("Minimal Window Indicator", "Clean text pills showing current & other window numbers."),
                ("Classic Tab Style", "Full shaded background tabs with borders."),
            ]
            curr_win = self.cfg.get("windows", {}).get("mode", "hidden")
            for idx, (name, desc) in enumerate(options):
                is_curr = (
                    (idx == 0 and curr_win == "hidden") or
                    (idx == 1 and curr_win == "minimal") or
                    (idx == 2 and curr_win == "tabs")
                )
                is_sel = (idx == self.selected_subitem and self.active_pane == 1)

                marker = "● " if is_curr else "○ "
                line1 = f"{marker}{name}"
                line2 = f"    {desc}"

                row = y + (idx * 3)
                if row + 1 < y + height:
                    if is_sel:
                        self.stdscr.addstr(row, x, line1.ljust(width), curses.A_BOLD | curses.color_pair(5))
                    elif is_curr:
                        self.stdscr.addstr(row, x, line1, curses.A_BOLD | curses.color_pair(2))
                    else:
                        self.stdscr.addstr(row, x, line1, curses.color_pair(8))
                    self.stdscr.addstr(row + 1, x, line2[:width], curses.color_pair(1))

        elif cat == 6:  # Sim alert
            options = [
                ("Normal Steady State", "Simulate standard healthy state (quiet status bar, only load/swap)"),
                ("Trigger Alert State", "Simulate thermal throttling (82%), low disk (12G), discharging battery (18%), CPU spike (94%)"),
            ]
            for idx, (name, desc) in enumerate(options):
                is_curr = (idx == 1 and self.sim_alerts) or (idx == 0 and not self.sim_alerts)
                is_sel = (idx == self.selected_subitem and self.active_pane == 1)

                marker = "● " if is_curr else "○ "
                line1 = f"{marker}{name}"
                line2 = f"    {desc}"

                row = y + (idx * 3)
                if row + 1 < y + height:
                    if is_sel:
                        self.stdscr.addstr(row, x, line1.ljust(width), curses.A_BOLD | curses.color_pair(5))
                    elif is_curr:
                        self.stdscr.addstr(row, x, line1, curses.A_BOLD | curses.color_pair(3 if self.sim_alerts else 2))
                    else:
                        self.stdscr.addstr(row, x, line1, curses.color_pair(8))
                    self.stdscr.addstr(row + 1, x, line2[:width], curses.color_pair(1))

        elif cat == 7:  # Save
            self.stdscr.addstr(y, x, "▶ Press [Enter] or [s] to save configuration and reload tmux live!", curses.A_BOLD | curses.color_pair(2))
            self.stdscr.addstr(y + 2, x, "Configuration will be written to:", curses.color_pair(1))
            self.stdscr.addstr(y + 3, x + 2, "• ~/.config/tmux-sentinel/config.json", curses.color_pair(8))
            self.stdscr.addstr(y + 4, x + 2, "• ~/.config/tmux-sentinel/sentinel.conf", curses.color_pair(8))
            self.stdscr.addstr(y + 5, x + 2, "• ~/.config/tmux-sentinel/env.sh", curses.color_pair(8))


def start_tui():
    """Entrypoint to launch the curses TUI."""
    curses.wrapper(lambda stdscr: SentinelTUI(stdscr).run())
