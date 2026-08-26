"""Interactive curses customizer.

The preview inside this TUI is the output of ``bin/sentinel-status --simulate``,
converted to curses attribute runs, so it cannot disagree with the real bar.
Every mutation goes through :mod:`cli.options`, keeping tmux options the single
source of truth.
"""

from __future__ import annotations

import curses
import os
import sys
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional, Tuple

from . import options as O
from . import renderer
from . import themes
from .renderer import Span, Style, display_width, pad_to_width, truncate_to_width

MIN_WIDTH = 80
MIN_HEIGHT = 24

# Chrome colour pairs; dynamic preview pairs are allocated above these.
P_ACCENT = 1
P_OK = 2
P_WARN = 3
P_ALERT = 4
P_SELECT = 5
P_DIM = 6
P_TEXT = 7
CHROME_PAIRS = 8


# --------------------------------------------------------------------------- #
# Colour allocation
# --------------------------------------------------------------------------- #

#: curses colour numbers 0-15, in the order of the ANSI palette.
_ANSI16 = list(renderer.NAMED_COLORS.values())


def _nearest(rgb: Tuple[int, int, int], table: List[Tuple[int, int, int]]) -> int:
    best, best_d = 0, None
    for idx, cand in enumerate(table):
        d = ((rgb[0] - cand[0]) ** 2 + (rgb[1] - cand[1]) ** 2
             + (rgb[2] - cand[2]) ** 2)
        if best_d is None or d < best_d:
            best, best_d = idx, d
    return best


def _xterm256_nearest(rgb: Tuple[int, int, int]) -> int:
    levels = (0, 95, 135, 175, 215, 255)
    cube = tuple(_nearest((c, c, c), [(l, l, l) for l in levels]) for c in rgb)
    cube_idx = 16 + 36 * cube[0] + 6 * cube[1] + cube[2]
    cube_rgb = (levels[cube[0]], levels[cube[1]], levels[cube[2]])
    grey_val = (rgb[0] + rgb[1] + rgb[2]) // 3
    grey_step = min(23, max(0, (grey_val - 8) // 10))
    grey_rgb = (8 + grey_step * 10,) * 3
    d_cube = sum((a - b) ** 2 for a, b in zip(rgb, cube_rgb))
    d_grey = sum((a - b) ** 2 for a, b in zip(rgb, grey_rgb))
    return cube_idx if d_cube <= d_grey else 232 + grey_step


class ColorAllocator:
    """Maps tmux colour specs onto curses colours and pairs, on demand.

    Truecolor is reproduced with ``init_color`` when the terminal allows
    redefinition; otherwise the nearest xterm-256 or ANSI-16 colour is used.  A
    terminal reporting ``COLORS == 8`` still works, it just looks coarse.
    """

    def __init__(self) -> None:
        try:
            curses.start_color()
        except curses.error:
            pass
        self.has_color = curses.has_colors()
        try:
            curses.use_default_colors()
            self.default_bg_ok = True
        except curses.error:
            self.default_bg_ok = False
        self.colors = getattr(curses, "COLORS", 8) if self.has_color else 0
        self.pairs = getattr(curses, "COLOR_PAIRS", 1) if self.has_color else 1
        try:
            self.can_change = bool(curses.can_change_color()) and self.colors >= 32
        except curses.error:
            self.can_change = False
        self._next_color = 16
        self._next_pair = CHROME_PAIRS + 1
        self._colors: Dict[str, int] = {}
        self._pairs: Dict[Tuple[int, int], int] = {}
        self.exhausted = False

    # -- colours ----------------------------------------------------------- #

    def color(self, spec: Optional[str]) -> int:
        """curses colour number for a tmux colour spec; -1 means terminal default."""
        if not self.has_color or spec is None or spec == "default":
            return -1
        cached = self._colors.get(spec)
        if cached is not None:
            return cached
        rgb = renderer.color_to_rgb(spec)
        if rgb is None:
            index = -1
        elif self.can_change and self._next_color < self.colors:
            index = self._next_color
            self._next_color += 1
            try:
                curses.init_color(
                    index,
                    rgb[0] * 1000 // 255, rgb[1] * 1000 // 255, rgb[2] * 1000 // 255,
                )
            except curses.error:
                self.can_change = False
                self._next_color -= 1
                index = self._fallback_index(rgb)
        else:
            index = self._fallback_index(rgb)
        self._colors[spec] = index
        return index

    def _fallback_index(self, rgb: Tuple[int, int, int]) -> int:
        if self.colors >= 256:
            return _xterm256_nearest(rgb)
        if self.colors >= 16:
            return _nearest(rgb, _ANSI16)
        if self.colors >= 8:
            return _nearest(rgb, _ANSI16[:8])
        return -1

    # -- pairs -------------------------------------------------------------- #

    def pair(self, fg: Optional[str], bg: Optional[str]) -> int:
        """Colour-pair attribute for the given tmux fg/bg specs."""
        if not self.has_color:
            return 0
        key = (self.color(fg), self.color(bg))
        cached = self._pairs.get(key)
        if cached is not None:
            return curses.color_pair(cached)
        if self._next_pair >= self.pairs:
            self.exhausted = True
            return 0
        number = self._next_pair
        self._next_pair += 1
        fg_idx, bg_idx = key
        if bg_idx == -1 and not self.default_bg_ok:
            bg_idx = 0
        if fg_idx == -1 and not self.default_bg_ok:
            fg_idx = 7
        try:
            curses.init_pair(number, fg_idx, bg_idx)
        except curses.error:
            self._pairs[key] = 0
            return 0
        self._pairs[key] = number
        return curses.color_pair(number)

    def attr(self, style: Style) -> int:
        attr = self.pair(style.fg, style.bg)
        if style.bold:
            attr |= curses.A_BOLD
        return attr


# --------------------------------------------------------------------------- #
# Menu model
# --------------------------------------------------------------------------- #


@dataclass
class Item:
    label: str
    detail: str = ""
    current: bool = False
    swatch: Optional[str] = None
    action: Optional[Callable[[], str]] = None
    adjust: Optional[Callable[[int], str]] = None


@dataclass
class Category:
    title: str
    build: Callable[[], List[Item]]
    hint: str = ""
    scroll: int = 0
    cursor: int = 0


THRESHOLDS = (
    ("disk_warn_gb", "Disk warning (GB free)", 5),
    ("disk_crit_gb", "Disk critical (GB free)", 5),
    ("cpu_warn_pct", "CPU warning (%)", 5),
    ("cpu_crit_pct", "CPU critical (%)", 5),
    ("battery_warn_pct", "Battery warning (%)", 5),
    ("battery_crit_pct", "Battery critical (%)", 5),
)

SEGMENT_HELP = {
    "thermal": "thermal pressure alert",
    "sleep_risk": "idle-sleep watchdog",
    "disk": "free disk space",
    "battery": "charge level while discharging",
    "cpu": "CPU utilisation",
    "memory": "swap use and memory pressure",
    "multi_client": "shown when more than one client is attached",
    "clock": "the time",
}

#: Why each segment might want a visible resting state.  The three segments
#: with no quiet state say so, rather than pretending the flag does something.
ALWAYS_HELP = {
    "thermal": "resting thermal reading, not just alerts",
    "sleep_risk": "resting idle-sleep countdown",
    "disk": "free space while it is still healthy",
    "battery": "charge level while not discharging",
    "cpu": "inert: cpu has no quiet state and always renders",
    "memory": "inert: memory has no quiet state and always renders",
    "multi_client": "client count even with one client attached",
    "clock": "inert: the clock has no quiet state and always renders",
}

WINDOW_HELP = (
    ("hidden", "Zen: no window list at all"),
    ("minimal", "Compact text window indicators"),
    ("tabs", "Classic shaded tabs"),
)


# --------------------------------------------------------------------------- #
# TUI
# --------------------------------------------------------------------------- #


class SentinelTUI:
    def __init__(self, stdscr: "curses.window", opts: O.Options) -> None:
        self.stdscr = stdscr
        self.opts = opts
        self.baseline = opts.as_dict()
        self.sim = "healthy"
        self.message = ""
        self.message_kind = P_OK
        self.pane = 0
        self.cat_index = 0
        self.confirm: Optional[str] = None
        self.preview_error: Optional[str] = None
        self._preview_cache: Dict[Tuple[str, str], List[Span]] = {}

        curses.curs_set(0)
        self.colors = ColorAllocator()
        self._init_chrome()
        stdscr.keypad(True)
        stdscr.timeout(-1)  # blocking: no idle redraw loop

        self.categories = self._build_categories()

    # -- setup ------------------------------------------------------------- #

    def _init_chrome(self) -> None:
        if not self.colors.has_color:
            return
        base = -1 if self.colors.default_bg_ok else curses.COLOR_BLACK
        spec = (
            (P_ACCENT, curses.COLOR_CYAN, base),
            (P_OK, curses.COLOR_GREEN, base),
            (P_WARN, curses.COLOR_YELLOW, base),
            (P_ALERT, curses.COLOR_RED, base),
            (P_SELECT, curses.COLOR_BLACK, curses.COLOR_CYAN),
            (P_DIM, curses.COLOR_BLUE, base),
            (P_TEXT, curses.COLOR_WHITE, base),
        )
        for number, fg, bg in spec:
            try:
                curses.init_pair(number, fg, bg)
            except curses.error:
                pass

    def _build_categories(self) -> List[Category]:
        return [
            Category("Theme", self._items_theme),
            Category("Segments", self._items_segments,
                     "Enter/Space toggles a segment"),
            Category("Resting state", self._items_always,
                     "Enter/Space flips whether a segment shows when quiet"),
            Category("Glyphs", self._items_glyphs),
            Category("Position", self._items_position),
            Category("Window list", self._items_windows),
            Category("Thresholds", self._items_thresholds,
                     "-/+ adjusts the selected threshold"),
            Category("Simulation", self._items_sim),
            Category("Save & apply", self._items_save),
        ]

    # -- item builders ------------------------------------------------------ #

    def _items_theme(self) -> List[Item]:
        current = self.opts.get("theme")
        items = []
        for stem in themes.list_themes():
            palette = themes.load_palette(stem)
            items.append(Item(
                label=palette["name"],
                detail=palette["description"],
                current=(stem == current),
                swatch=palette["accent"],
                action=lambda s=stem: self._stage("theme", s),
            ))
        return items

    def _items_segments(self) -> List[Item]:
        enabled = set(self.opts.segment_list())
        return [
            Item(
                label=name,
                detail=("on  " if name in enabled else "off ") + SEGMENT_HELP[name],
                current=(name in enabled),
                action=lambda n=name: self._toggle(n),
            )
            for name in O.SEGMENTS
        ]

    def _items_always(self) -> List[Item]:
        resting = set(self.opts.always_list())
        return [
            Item(
                label=name,
                detail=("on  " if name in resting else "off ") + ALWAYS_HELP[name],
                current=(name in resting),
                action=lambda n=name: self._toggle_always(n),
            )
            for name in O.SEGMENTS
        ]

    def _items_glyphs(self) -> List[Item]:
        current = self.opts.get("glyphs")
        items = []
        for mode in O.GLYPH_MODES:
            glyphs = themes.load_glyphs(mode)
            sample = " ".join(
                glyphs[k] for k in
                ("thermal", "sleep", "disk", "battery_full", "cpu", "memory",
                 "clients")
            )
            items.append(Item(
                label=glyphs["name"],
                detail=sample,
                current=(mode == current),
                action=lambda m=mode: self._stage("glyphs", m),
            ))
        return items

    def _items_position(self) -> List[Item]:
        current = self.opts.get("position")
        return [
            Item("Top", "keeps the bottom of the pane free for prompts",
                 current == "top", action=lambda: self._stage("position", "top")),
            Item("Bottom", "traditional tmux placement",
                 current == "bottom",
                 action=lambda: self._stage("position", "bottom")),
        ]

    def _items_windows(self) -> List[Item]:
        current = self.opts.get("windows")
        return [
            Item(mode, detail, current == mode,
                 action=lambda m=mode: self._stage("windows", m))
            for mode, detail in WINDOW_HELP
        ]

    def _items_thresholds(self) -> List[Item]:
        items = []
        for key, label, step in THRESHOLDS:
            items.append(Item(
                label=f"{label}: {self.opts.get(key)}",
                detail=O.SPEC_BY_KEY[key].domain,
                current=False,
                adjust=lambda delta, k=key, s=step: self._adjust(k, delta * s),
            ))
        return items

    def _items_sim(self) -> List[Item]:
        return [
            Item("Healthy", "nominal thermals, charged, quiet bar",
                 self.sim == "healthy", action=lambda: self._set_sim("healthy")),
            Item("Alert", "throttled, sleep risk, low disk, discharging, hot CPU",
                 self.sim == "alert", action=lambda: self._set_sim("alert")),
        ]

    def _items_save(self) -> List[Item]:
        dirty = self.opts.dirty_keys
        detail = (", ".join(dirty) if dirty else "nothing changed")
        return [
            Item("Save & apply", detail, False, action=self._save),
            Item("Discard changes", "revert to the saved settings", False,
                 action=self._discard),
        ]

    # -- mutations ---------------------------------------------------------- #

    def _stage(self, key: str, value: str) -> str:
        try:
            self.opts.stage(key, value)
        except O.OptionError as exc:
            return str(exc)
        self._preview_cache.clear()
        return f"{key} = {value}"

    def _toggle(self, name: str) -> str:
        enabled = self.opts.toggle_segment(name)
        self._preview_cache.clear()
        return f"segment {name} {'enabled' if enabled else 'disabled'}"

    def _toggle_always(self, name: str) -> str:
        try:
            on = self.opts.toggle_always(name)
        except O.OptionError as exc:
            return str(exc)
        self._preview_cache.clear()
        if name in O.INERT_ALWAYS:
            return (f"always_{name} = {1 if on else 0}, but {name} has no quiet "
                    "state and renders either way")
        if on:
            return f"{name} now shows a resting value"
        return f"{name} is now quiet until it has something to report"

    def _adjust(self, key: str, delta: int) -> str:
        value = self.opts.int_of(key) + delta
        spec = O.SPEC_BY_KEY[key]
        candidate = str(max(0, value))
        if not spec.check(candidate, self.opts):
            return f"{key} would leave its range ({spec.domain})"
        return self._stage(key, candidate)

    def _set_sim(self, sim: str) -> str:
        self.sim = sim
        return f"simulating the {sim} state"

    def _save(self) -> str:
        result = self.opts.commit()
        self.baseline = self.opts.as_dict()
        self._preview_cache.clear()
        if result.errors:
            self.message_kind = P_ALERT
            return "; ".join(result.errors)
        return "saved to options.conf, regenerated and reloaded tmux"

    def _discard(self) -> str:
        self.opts = O.load(quiet=True)
        self.baseline = self.opts.as_dict()
        self._preview_cache.clear()
        return "discarded unsaved changes"

    # -- state -------------------------------------------------------------- #

    @property
    def dirty(self) -> bool:
        return self.opts.as_dict() != self.baseline

    # -- main loop ---------------------------------------------------------- #

    def run(self) -> int:
        while True:
            self._draw()
            try:
                ch = self.stdscr.getch()
            except KeyboardInterrupt:
                return 130
            except curses.error:
                continue

            if ch == curses.KEY_RESIZE:
                self._preview_cache.clear()
                self.stdscr.clear()
                continue

            if self.confirm is not None:
                if ch in (ord("y"), ord("Y")):
                    return 0
                if ch in (ord("s"), ord("S")):
                    self._message(self._save())
                    self.confirm = None
                    return 0
                self.confirm = None
                continue

            if ch in (ord("q"), ord("Q"), 27):
                if self.dirty:
                    self.confirm = "quit"
                    continue
                return 0
            if ch == 3:  # Ctrl-C delivered as a key
                return 130
            if ch in (ord("s"), ord("S")):
                self._message(self._save())
            elif ch in (ord("a"), ord("A")):
                self._message(self._set_sim(
                    "alert" if self.sim == "healthy" else "healthy"))
            elif ch in (ord("t"), ord("T")):
                self._message(self._cycle_theme())
            elif ch in (curses.KEY_UP, ord("k")):
                self._move(-1)
            elif ch in (curses.KEY_DOWN, ord("j")):
                self._move(1)
            elif ch in (curses.KEY_LEFT, ord("h")):
                self.pane = 0
            elif ch in (curses.KEY_RIGHT, ord("l"), ord("\t")):
                self.pane = 1
            elif ch in (ord("-"), ord("_")):
                self._adjust_selected(-1)
            elif ch in (ord("+"), ord("=")):
                self._adjust_selected(1)
            elif ch in (curses.KEY_ENTER, ord("\n"), ord("\r"), ord(" ")):
                self._activate()

    def _cycle_theme(self) -> str:
        available = themes.list_themes()
        if not available:
            return "no palettes found in themes/"
        current = self.opts.get("theme")
        index = available.index(current) if current in available else -1
        return self._stage("theme", available[(index + 1) % len(available)])

    def _message(self, text: str, kind: int = P_OK) -> None:
        self.message = text
        self.message_kind = kind

    def _category(self) -> Category:
        return self.categories[self.cat_index]

    def _move(self, delta: int) -> None:
        if self.pane == 0:
            self.cat_index = (self.cat_index + delta) % len(self.categories)
            return
        cat = self._category()
        count = len(cat.build())
        if count:
            cat.cursor = (cat.cursor + delta) % count

    def _activate(self) -> None:
        cat = self._category()
        items = cat.build()
        if self.pane == 0 and items:
            self.pane = 1
            return
        if not items:
            return
        item = items[min(cat.cursor, len(items) - 1)]
        if item.action is not None:
            self._message(item.action())
        elif item.adjust is not None:
            self._message(item.adjust(1))

    def _adjust_selected(self, direction: int) -> None:
        cat = self._category()
        items = cat.build()
        if not items:
            return
        item = items[min(cat.cursor, len(items) - 1)]
        if item.adjust is not None:
            self._message(item.adjust(direction))

    # -- drawing ------------------------------------------------------------ #

    def _addstr(self, y: int, x: int, text: str, attr: int = 0) -> int:
        """Write clipped text; returns the number of columns consumed."""
        h, w = self.stdscr.getmaxyx()
        if y < 0 or y >= h or x >= w:
            return 0
        clipped = truncate_to_width(text, w - x)
        if not clipped:
            return 0
        try:
            self.stdscr.addstr(y, x, clipped, attr)
        except curses.error:
            # Writing the final cell of the window is allowed to fail.
            pass
        return display_width(clipped)

    def _draw(self) -> None:
        self.stdscr.erase()
        h, w = self.stdscr.getmaxyx()
        if w < MIN_WIDTH or h < MIN_HEIGHT:
            self._addstr(0, 0, f"Terminal is {w}x{h}.", curses.A_BOLD)
            self._addstr(
                1, 0,
                f"sentinel customize needs at least "
                f"{MIN_WIDTH}x{MIN_HEIGHT}. Resize, or use `sentinel set`.",
            )
            self.stdscr.refresh()
            return

        title = " tmux-sentinel customizer "
        self._addstr(0, 2, title, curses.A_BOLD | curses.color_pair(P_ACCENT))
        marker = "  *  unsaved changes" if self.dirty else ""
        if marker:
            self._addstr(0, 2 + display_width(title), marker,
                         curses.A_BOLD | curses.color_pair(P_WARN))
        keys = "[s]ave  [q]uit"
        self._addstr(0, max(0, w - display_width(keys) - 2), keys,
                     curses.color_pair(P_DIM))

        self._draw_preview(2, 2, w - 4)

        msg_y = 6
        if self.message:
            self._addstr(msg_y, 4, self.message,
                         curses.A_BOLD | curses.color_pair(self.message_kind))
        else:
            self._addstr(msg_y, 4, "-" * (w - 8), curses.color_pair(P_DIM))

        body_y = 8
        body_h = h - body_y - 3
        col_w = 22
        self._draw_categories(body_y, 2, col_w, body_h)
        self._draw_items(body_y, col_w + 4, w - col_w - 6, body_h)

        if self.confirm is not None:
            prompt = (f"Discard {len(self.opts.dirty_keys)} unsaved change(s)? "
                      "[y] discard  [s] save and quit  [any other key] cancel")
            self._addstr(h - 2, 2, prompt,
                         curses.A_BOLD | curses.color_pair(P_ALERT))
        else:
            help_text = (
                "[jk/arrows] move  [tab] pane  [enter] select  "
                "[-/+] threshold  [t] theme  [a] sim  [s] save  [q] quit"
            )
            self._addstr(h - 2, 2, help_text, curses.color_pair(P_DIM))
        self.stdscr.refresh()

    def _preview_spans(self, bar_width: int) -> List[Span]:
        key = (self.sim, str(bar_width))
        cached = self._preview_cache.get(key)
        if cached is not None:
            return cached
        try:
            bar = renderer.compose_bar(self.opts, bar_width, sim=self.sim)
            spans = renderer.parse_tmux(bar)
            self.preview_error = None
        except O.EngineMissing as exc:
            self.preview_error = str(exc).split("\n")[0]
            spans = []
        except (RuntimeError, themes.DataFileError, OSError) as exc:
            self.preview_error = str(exc)
            spans = []
        self._preview_cache[key] = spans
        return spans

    def _draw_preview(self, y: int, x: int, width: int) -> None:
        accent = curses.color_pair(P_ACCENT)
        heading = "- Live status bar preview "
        top = "+" + heading + "-" * max(0, width - 2 - len(heading)) + "+"
        self._addstr(y, x, top[:width], accent)
        for row in (y + 1, y + 2):
            self._addstr(row, x, "|", accent)
            self._addstr(row, x + width - 1, "|", accent)
        self._addstr(y + 3, x, "+" + "-" * (width - 2) + "+", accent)

        bar_width = width - 4
        if bar_width < 8:
            return
        spans = self._preview_spans(bar_width)
        if self.preview_error is not None:
            self._addstr(y + 1, x + 2,
                         pad_to_width(self.preview_error, bar_width),
                         curses.color_pair(P_ALERT))
        else:
            palette = themes.load_palette(self.opts.get("theme"))
            bg = palette["bg"]
            col = x + 2
            used = 0
            for span in spans:
                if used >= bar_width:
                    break
                text = truncate_to_width(span.text, bar_width - used)
                if not text:
                    continue
                style = Style(span.style.fg, span.style.bg or bg, span.style.bold)
                used += self._addstr(y + 1, col + used, text,
                                     self.colors.attr(style))
            if used < bar_width:
                self._addstr(y + 1, col + used, " " * (bar_width - used),
                             self.colors.attr(Style(None, bg, False)))

        palette = themes.load_palette(self.opts.get("theme"))
        glyphs = themes.load_glyphs(self.opts.get("glyphs"))
        label = (f"{palette['name']} | {glyphs['name']} | "
                 f"simulating: {self.sim} | position: {self.opts.get('position')}")
        if not self.colors.can_change and self.colors.colors < 256:
            label += f" | {self.colors.colors}-colour terminal, colours approximated"
        self._addstr(y + 2, x + 2, truncate_to_width(label, bar_width),
                     curses.color_pair(P_WARN if self.sim == "alert" else P_OK))

    def _draw_categories(self, y: int, x: int, width: int, height: int) -> None:
        for idx, cat in enumerate(self.categories):
            if idx >= height:
                break
            selected = idx == self.cat_index
            prefix = "> " if selected else "  "
            line = pad_to_width(prefix + cat.title, width)
            if selected and self.pane == 0:
                attr = curses.A_BOLD | curses.color_pair(P_SELECT)
            elif selected:
                attr = curses.A_BOLD | curses.color_pair(P_ACCENT)
            else:
                attr = curses.color_pair(P_TEXT)
            self._addstr(y + idx, x, line, attr)

    def _draw_items(self, y: int, x: int, width: int, height: int) -> None:
        cat = self._category()
        items = cat.build()
        if not items:
            return
        cat.cursor = min(cat.cursor, len(items) - 1)
        rows = max(1, height - (1 if cat.hint else 0))
        if cat.cursor < cat.scroll:
            cat.scroll = cat.cursor
        elif cat.cursor >= cat.scroll + rows:
            cat.scroll = cat.cursor - rows + 1
        cat.scroll = max(0, min(cat.scroll, max(0, len(items) - rows)))

        for row in range(rows):
            idx = cat.scroll + row
            if idx >= len(items):
                break
            item = items[idx]
            selected = idx == cat.cursor and self.pane == 1
            mark = "*" if item.current else " "
            col = x
            if selected:
                attr = curses.A_BOLD | curses.color_pair(P_SELECT)
            elif item.current:
                attr = curses.A_BOLD | curses.color_pair(P_OK)
            else:
                attr = curses.color_pair(P_TEXT)
            body = f"{mark} "
            col += self._addstr(y + row, col, body, attr)
            if item.swatch:
                # Two blanks painted with the theme's accent as background:
                # always exactly two columns wide, whatever the font does.
                col += self._addstr(y + row, col, "  ",
                                    self.colors.pair(None, item.swatch))
                col += self._addstr(y + row, col, " ", attr)
            remaining = width - (col - x)
            text = item.label
            if item.detail:
                text = f"{item.label}  {item.detail}"
            self._addstr(y + row, col, pad_to_width(text, max(0, remaining)), attr)

        if cat.hint:
            self._addstr(y + rows, x, truncate_to_width(cat.hint, width),
                         curses.color_pair(P_DIM))
        if len(items) > rows:
            self._addstr(y + rows, x + width - 12,
                         f"{cat.cursor + 1}/{len(items)}",
                         curses.color_pair(P_DIM))


def start_tui() -> int:
    """Launch the customizer.  Returns a process exit code."""
    if not sys.stdout.isatty():
        print("sentinel: customize needs a terminal; try `sentinel preview`",
              file=sys.stderr)
        return 1
    try:
        opts = O.load()
    except O.ConfigError as exc:
        print(f"sentinel: error: {exc}", file=sys.stderr)
        return 1
    if "ESCDELAY" not in os.environ:
        os.environ["ESCDELAY"] = "25"
    try:
        return curses.wrapper(lambda scr: SentinelTUI(scr, opts).run())
    except KeyboardInterrupt:
        return 130
