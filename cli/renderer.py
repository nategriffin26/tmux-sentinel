"""Converter from the engine's tmux-format output to ANSI / curses runs.

There is no Python reimplementation of the status bar.  The right-hand segment
group is produced by ``bin/sentinel-status`` and this module only translates it
for terminal display (``sentinel preview``) or curses attribute runs (the TUI).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from . import options as opt_mod
from . import themes

# --------------------------------------------------------------------------- #
# Width
# --------------------------------------------------------------------------- #

_ZERO_WIDTH = frozenset("\u200b\u200c\u200d\u2060\ufeff\ufe0e\ufe0f")


def display_width(text: str) -> int:
    """Terminal column count of ``text``.

    ``len()`` is wrong for the data this project renders: the unicode glyph set
    contains wide characters and both glyph sets contain variation selectors and
    combining marks.  East-Asian ``W``/``F`` count as two columns; combining
    marks, zero-width characters and U+FE0F count as zero.
    """
    width = 0
    for ch in text:
        if ch in _ZERO_WIDTH or unicodedata.combining(ch):
            continue
        cat = unicodedata.category(ch)
        if cat in ("Mn", "Me", "Cf"):
            continue
        if cat == "Cc":
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def truncate_to_width(text: str, width: int) -> str:
    """Longest prefix of ``text`` whose display width is <= ``width``."""
    if width <= 0:
        return ""
    total = 0
    for idx, ch in enumerate(text):
        step = display_width(ch)
        if total + step > width:
            return text[:idx]
        total += step
    return text


def pad_to_width(text: str, width: int) -> str:
    """Pad or truncate ``text`` to exactly ``width`` display columns."""
    clipped = truncate_to_width(text, width)
    return clipped + " " * (width - display_width(clipped))


# --------------------------------------------------------------------------- #
# Colour
# --------------------------------------------------------------------------- #


def hex_to_rgb(hex_str: str) -> Tuple[int, int, int]:
    """Convert ``#rgb`` or ``#rrggbb`` to an (r, g, b) triple."""
    clean = hex_str.lstrip("#")
    if len(clean) == 3:
        clean = "".join(c * 2 for c in clean)
    if len(clean) != 6:
        return (200, 200, 200)
    try:
        return (int(clean[0:2], 16), int(clean[2:4], 16), int(clean[4:6], 16))
    except ValueError:
        return (200, 200, 200)


#: tmux's named colours, as approximate sRGB.
NAMED_COLORS: Dict[str, Tuple[int, int, int]] = {
    "black": (0, 0, 0),
    "red": (205, 0, 0),
    "green": (0, 205, 0),
    "yellow": (205, 205, 0),
    "blue": (0, 0, 238),
    "magenta": (205, 0, 205),
    "cyan": (0, 205, 205),
    "white": (229, 229, 229),
    "brightblack": (127, 127, 127),
    "brightred": (255, 0, 0),
    "brightgreen": (0, 255, 0),
    "brightyellow": (255, 255, 0),
    "brightblue": (92, 92, 255),
    "brightmagenta": (255, 0, 255),
    "brightcyan": (0, 255, 255),
    "brightwhite": (255, 255, 255),
}

_COLOUR_INDEX_RE = re.compile(r"^colou?r(\d+)$")


def color_to_rgb(value: str) -> Optional[Tuple[int, int, int]]:
    """Resolve a tmux colour spec to RGB, or None for ``default``/unknown."""
    if not value or value == "default":
        return None
    if value.startswith("#"):
        return hex_to_rgb(value)
    lowered = value.lower()
    if lowered in NAMED_COLORS:
        return NAMED_COLORS[lowered]
    m = _COLOUR_INDEX_RE.match(lowered)
    if m:
        return _xterm256_to_rgb(int(m.group(1)))
    return None


def _xterm256_to_rgb(index: int) -> Tuple[int, int, int]:
    if index < 16:
        order = list(NAMED_COLORS.values())
        return order[index] if index < len(order) else (200, 200, 200)
    if index < 232:
        index -= 16
        levels = (0, 95, 135, 175, 215, 255)
        return (levels[index // 36], levels[(index // 6) % 6], levels[index % 6])
    grey = 8 + (index - 232) * 10
    return (grey, grey, grey)


# --------------------------------------------------------------------------- #
# tmux format parsing
# --------------------------------------------------------------------------- #


@dataclass
class Style:
    fg: Optional[str] = None
    bg: Optional[str] = None
    bold: bool = False

    def copy(self) -> "Style":
        return Style(self.fg, self.bg, self.bold)


@dataclass
class Span:
    text: str
    style: Style


_TAG_RE = re.compile(r"#\[([^\]]*)\]")


def _apply_tag(style: Style, body: str) -> Style:
    new = style.copy()
    for part in body.split(","):
        part = part.strip()
        if not part:
            continue
        if part.startswith("fg="):
            new.fg = part[3:]
        elif part.startswith("bg="):
            new.bg = part[3:]
        elif part in ("bold", "bright"):
            new.bold = True
        elif part in ("nobold", "nobright"):
            new.bold = False
        elif part in ("default", "none"):
            new = Style()
    return new


def parse_tmux(text: str) -> List[Span]:
    """Split a tmux-format string into styled spans.  ``##`` unescapes to ``#``."""
    spans: List[Span] = []
    style = Style()
    buffer: List[str] = []
    i = 0
    length = len(text)
    while i < length:
        ch = text[i]
        if ch == "#" and i + 1 < length:
            nxt = text[i + 1]
            if nxt == "[":
                m = _TAG_RE.match(text, i)
                if m:
                    if buffer:
                        spans.append(Span("".join(buffer), style))
                        buffer = []
                    style = _apply_tag(style, m.group(1))
                    i = m.end()
                    continue
            elif nxt == "#":
                buffer.append("#")
                i += 2
                continue
        buffer.append(ch)
        i += 1
    if buffer:
        spans.append(Span("".join(buffer), style))
    return spans


def plain_text(text: str) -> str:
    """Strip tmux ``#[...]`` markup, leaving the printable characters."""
    return "".join(span.text for span in parse_tmux(text))


def spans_to_ansi(spans: List[Span], default_bg: Optional[str] = None) -> str:
    """Render spans as ANSI.  ``default_bg`` backs spans that set no background."""
    bg_fallback = "49"
    if default_bg and default_bg != "default":
        rgb = color_to_rgb(default_bg)
        if rgb is not None:
            bg_fallback = f"48;2;{rgb[0]};{rgb[1]};{rgb[2]}"
    out: List[str] = []
    for span in spans:
        codes: List[str] = []
        if span.style.fg is None or span.style.fg == "default":
            codes.append("39")
        else:
            rgb = color_to_rgb(span.style.fg)
            codes.append("39" if rgb is None else f"38;2;{rgb[0]};{rgb[1]};{rgb[2]}")
        if span.style.bg is None or span.style.bg == "default":
            codes.append(bg_fallback)
        else:
            rgb = color_to_rgb(span.style.bg)
            codes.append(
                bg_fallback if rgb is None else f"48;2;{rgb[0]};{rgb[1]};{rgb[2]}"
            )
        codes.append("1" if span.style.bold else "22")
        out.append("\033[" + ";".join(codes) + "m" + span.text)
    return "".join(out)


def tmux_to_ansi(tmux_str: str, default_bg: Optional[str] = None) -> str:
    """Convert a tmux-format string containing ``#[...]`` into ANSI."""
    return spans_to_ansi(parse_tmux(tmux_str), default_bg) + "\033[0m"


_ANSI_RE = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def strip_ansi(text: str) -> str:
    """Remove ANSI escape sequences, leaving the printable characters."""
    return _ANSI_RE.sub("", text)


# --------------------------------------------------------------------------- #
# Engine output
# --------------------------------------------------------------------------- #

SIM_STATES = ("healthy", "alert")


def engine_segments(opts: "opt_mod.Options", sim: str) -> str:
    """The right-hand segment group, rendered by ``bin/sentinel-status``.

    Raises :class:`options.EngineMissing` with an actionable message when the
    binary has not been built.  There is deliberately no Python fallback.
    """
    if sim not in SIM_STATES:
        raise ValueError(f"sim must be one of {SIM_STATES}, got {sim!r}")
    state_path = opt_mod.write_temp_state(opts)
    try:
        proc = opt_mod.run_engine(
            ["--simulate", sim, "--state", str(state_path)]
        )
    finally:
        try:
            state_path.unlink()
        except OSError:
            pass
    if proc.returncode != 0:
        detail = proc.stderr.strip() or f"exit {proc.returncode}"
        raise RuntimeError(f"sentinel-status --simulate {sim} failed: {detail}")
    return proc.stdout.rstrip("\n")


def separator_token(opts: "opt_mod.Options") -> str:
    """The exact structural separator the engine places between segments."""
    palette = themes.load_palette(opts.get("theme"))
    glyphs = themes.load_glyphs(opts.get("glyphs"))
    return f"#[fg={palette['sep']}]{glyphs['sep']}"


def split_segments(rendered: str, separator: str) -> List[str]:
    """Split engine output back into its rendered segments."""
    if not rendered:
        return []
    if not separator:
        return [rendered]
    return rendered.split(separator)


def render_left(opts: "opt_mod.Options", session_name: str) -> str:
    """The tmux ``status-left`` equivalent, for preview composition only."""
    palette = themes.load_palette(opts.get("theme"))
    accent = opts.get("accent")
    session = truncate_to_width(session_name, opts.int_of("session_max_length"))
    left = (
        f"#[fg={palette['accent']}]{accent}"
        f"#[fg={palette['fg']},bold] {session} #[default]"
    )
    mode = opts.get("windows")
    if mode == "minimal":
        left += (
            f"#[fg={palette['accent']},bold]1:dev"
            f"#[fg={palette['sep']}] | #[fg={palette['dim']}]2:sh #[default]"
        )
    elif mode == "tabs":
        left += (
            f"#[fg={palette['bg']},bg={palette['accent']},bold] 1 dev "
            f"#[fg={palette['fg']},bg={palette['mode_bg']}] 2 sh #[default]"
        )
    return left


def compose_bar(
    opts: "opt_mod.Options",
    width: int,
    sim: str = "healthy",
    session_name: str = "main",
) -> str:
    """Compose a full-width bar in tmux format: left + padding + engine segments.

    The result is exactly ``width`` display columns.  When the content does not
    fit, whole segments are dropped from the left of the segment group rather
    than emitting an over-wide line.
    """
    right = engine_segments(opts, sim)
    separator = separator_token(opts)
    segments = split_segments(right, separator)
    left = render_left(opts, session_name)

    left_w = display_width(plain_text(left))
    sep_w = display_width(plain_text(separator))

    def group_width(parts: List[str]) -> int:
        if not parts:
            return 0
        return sum(display_width(plain_text(p)) for p in parts) + sep_w * (
            len(parts) - 1
        )

    # Reserve one column of padding between the halves.
    while segments and left_w + 1 + group_width(segments) > width:
        segments.pop(0)

    right_out = separator.join(segments)
    right_w = group_width(segments)

    if left_w + right_w > width:
        # Nothing but the left half remains and it is still too wide.
        left = _truncate_tmux(left, max(width - right_w, 0))
        left_w = display_width(plain_text(left))

    pad = width - left_w - right_w
    if pad < 0:
        pad = 0
    return left + "#[default]" + " " * pad + right_out


def _truncate_tmux(tmux_str: str, width: int) -> str:
    """Clip a tmux-format string to ``width`` display columns, keeping markup."""
    out: List[str] = []
    used = 0
    for span in parse_tmux(tmux_str):
        style = span.style
        tag_parts = []
        if style.fg:
            tag_parts.append(f"fg={style.fg}")
        if style.bg:
            tag_parts.append(f"bg={style.bg}")
        if style.bold:
            tag_parts.append("bold")
        tag = f"#[{','.join(tag_parts)}]" if tag_parts else "#[default]"
        remaining = width - used
        if remaining <= 0:
            break
        clipped = truncate_to_width(span.text, remaining)
        if clipped:
            out.append(tag + clipped)
            used += display_width(clipped)
    return "".join(out)


def render_preview_bar(
    opts: "opt_mod.Options",
    width: int = 80,
    sim: str = "healthy",
    session_name: str = "main",
) -> str:
    """ANSI rendering of the real bar, exactly ``width`` display columns wide."""
    bar = compose_bar(opts, width, sim=sim, session_name=session_name)
    palette = themes.load_palette(opts.get("theme"))
    return tmux_to_ansi(bar, default_bg=palette["bg"])


def preview_width(rendered_ansi: str) -> int:
    """Measured display width of an ANSI-rendered bar."""
    return display_width(strip_ansi(rendered_ansi))
