"""Loader for the theme and glyph data files.

``themes/<stem>.palette`` and ``glyphs/<mode>.glyphs`` are the sole definition of
a palette / glyph set (CONTRACT SS7).  Python holds no second copy: there are no
``THEMES`` or ``GLYPH_SETS`` dict literals any more.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List

REPO_ROOT = Path(__file__).resolve().parent.parent
THEMES_DIR = REPO_ROOT / "themes"
GLYPHS_DIR = REPO_ROOT / "glyphs"

PALETTE_KEYS = (
    "name",
    "description",
    "bg",
    "fg",
    "dim",
    "val",
    "sep",
    "accent",
    "prefix",
    "copy_mode",
    "warn",
    "alert",
    "peach",
    "info",
    "border",
    "active_border",
    "message_bg",
    "mode_bg",
)

GLYPH_KEYS = (
    "name",
    "accent",
    "sep",
    "thermal",
    "sleep",
    "disk",
    "battery_full",
    "battery_mid",
    "battery_low",
    "cpu",
    "memory",
    "clients",
)

_KEY_RE = re.compile(r"[a-z0-9_]+")


class DataFileError(Exception):
    """A palette or glyph file is missing or incomplete."""


def parse_kv_file(path: Path) -> Dict[str, str]:
    """Parse the CONTRACT SS1 ``key=value`` grammar.

    Only the terminating LF/CR is stripped: trailing whitespace inside a value is
    significant (``sep`` is literally ``" . "`` with both padding spaces).
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DataFileError(f"cannot read {path}: {exc}") from exc

    out: Dict[str, str] = {}
    for raw in text.split("\n"):
        line = raw.rstrip("\r")
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not _KEY_RE.fullmatch(key):
            continue
        out[key] = value
    return out


def _require(path: Path, data: Dict[str, str], keys, kind: str) -> Dict[str, str]:
    missing = [k for k in keys if k not in data]
    if missing:
        raise DataFileError(
            f"{path} is not a valid {kind}: missing key(s) {', '.join(missing)}"
        )
    return data


def list_themes() -> List[str]:
    """Filename stems of every available palette, sorted."""
    if not THEMES_DIR.is_dir():
        return []
    return sorted(p.stem for p in THEMES_DIR.glob("*.palette"))


def list_glyph_modes() -> List[str]:
    if not GLYPHS_DIR.is_dir():
        return []
    return sorted(p.stem for p in GLYPHS_DIR.glob("*.glyphs"))


_palette_cache: Dict[str, Dict[str, str]] = {}
_glyph_cache: Dict[str, Dict[str, str]] = {}


def load_palette(stem: str) -> Dict[str, str]:
    cached = _palette_cache.get(stem)
    if cached is not None:
        return cached
    path = THEMES_DIR / f"{stem}.palette"
    if not path.is_file():
        available = ", ".join(list_themes()) or "(none found)"
        raise DataFileError(f"unknown theme {stem!r}; available: {available}")
    data = _require(path, parse_kv_file(path), PALETTE_KEYS, "palette")
    _palette_cache[stem] = data
    return data


def load_glyphs(mode: str) -> Dict[str, str]:
    cached = _glyph_cache.get(mode)
    if cached is not None:
        return cached
    path = GLYPHS_DIR / f"{mode}.glyphs"
    if not path.is_file():
        available = ", ".join(list_glyph_modes()) or "(none found)"
        raise DataFileError(f"unknown glyph mode {mode!r}; available: {available}")
    data = _require(path, parse_kv_file(path), GLYPH_KEYS, "glyph set")
    _glyph_cache[mode] = data
    return data


def all_palettes() -> Dict[str, Dict[str, str]]:
    return {stem: load_palette(stem) for stem in list_themes()}
