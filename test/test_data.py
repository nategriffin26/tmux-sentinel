"""Theme and glyph data-file integrity.

These files are the single source of truth for palettes in v2, so a typo
here silently degrades every consumer. Cheap to check, so check everything.
"""

from __future__ import annotations

import re
import unicodedata
import unittest

from harness import GLYPH_MODES, REPO, THEMES, parse_kv

HEX = re.compile(r"\A#[0-9a-f]{6}\Z")

PALETTE_KEYS = {
    "name", "description", "bg", "fg", "dim", "val", "sep", "accent",
    "prefix", "copy_mode", "warn", "alert", "peach", "info", "border",
    "active_border", "message_bg", "mode_bg",
}
GLYPH_KEYS = {
    "name", "accent", "sep", "thermal", "sleep", "disk", "battery_full",
    "battery_mid", "battery_low", "cpu", "memory", "clients",
}
COLOUR_KEYS = PALETTE_KEYS - {"name", "description", "bg"}


def relative_luminance(hex_colour: str) -> float:
    raw = hex_colour.lstrip("#")
    channels = [int(raw[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4 for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast(a: str, b: str) -> float:
    la, lb = relative_luminance(a), relative_luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def load(path):
    return parse_kv(path.read_text(encoding="utf-8"))


class TestPalettes(unittest.TestCase):
    def test_twelve_themes_present(self):
        self.assertEqual(len(THEMES), 12, THEMES)

    def test_every_palette_is_complete_and_well_formed(self):
        for stem in THEMES:
            with self.subTest(theme=stem):
                pal = load(REPO / "themes" / f"{stem}.palette")
                self.assertEqual(set(pal), PALETTE_KEYS,
                                 f"key mismatch: {set(pal) ^ PALETTE_KEYS}")
                self.assertTrue(pal["name"].strip())
                self.assertTrue(pal["description"].strip())
                self.assertTrue(pal["bg"] == "default" or HEX.match(pal["bg"]),
                                f"bg={pal['bg']!r}")
                for key in COLOUR_KEYS:
                    self.assertRegex(pal[key], HEX, f"{stem}.{key}={pal[key]!r}")

    def test_dim_text_is_legible_against_the_theme_background(self):
        """`dim` carries the segment glyphs. Below ~3:1 they vanish.

        v1 shipped catppuccin-latte, tokyo-night and one-dark below that.
        """
        for stem in THEMES:
            with self.subTest(theme=stem):
                pal = load(REPO / "themes" / f"{stem}.palette")
                bg = "#1e1e2e" if pal["bg"] == "default" else pal["bg"]
                self.assertGreaterEqual(
                    contrast(pal["dim"], bg), 3.0,
                    f"{stem}: dim {pal['dim']} on {bg} is "
                    f"{contrast(pal['dim'], bg):.2f}:1")

    def test_alert_colours_are_distinguishable_from_normal_values(self):
        for stem in THEMES:
            with self.subTest(theme=stem):
                pal = load(REPO / "themes" / f"{stem}.palette")
                self.assertNotEqual(pal["alert"], pal["val"])
                self.assertNotEqual(pal["alert"], pal["dim"])


class TestGlyphSets(unittest.TestCase):
    def test_three_glyph_modes_present(self):
        self.assertEqual(set(GLYPH_MODES), {"nerd", "unicode", "ascii"})

    def test_every_glyph_set_is_complete(self):
        for mode in GLYPH_MODES:
            with self.subTest(mode=mode):
                glyphs = load(REPO / "glyphs" / f"{mode}.glyphs")
                self.assertEqual(set(glyphs), GLYPH_KEYS,
                                 f"key mismatch: {set(glyphs) ^ GLYPH_KEYS}")

    def test_separator_keeps_its_padding_spaces(self):
        """The separator's value is literally " · ".

        Any editor or generator that strips trailing whitespace welds the
        segments together, so pin it.
        """
        for mode in GLYPH_MODES:
            with self.subTest(mode=mode):
                sep = load(REPO / "glyphs" / f"{mode}.glyphs")["sep"]
                self.assertTrue(sep.startswith(" "), repr(sep))
                self.assertTrue(sep.endswith(" "), repr(sep))

    def test_no_double_width_glyphs(self):
        """Wide glyphs desynchronise every column calculation downstream.

        v1's unicode set used U+26A1 and U+1F465, both East-Asian-Wide.
        """
        for mode in GLYPH_MODES:
            glyphs = load(REPO / "glyphs" / f"{mode}.glyphs")
            for key, value in glyphs.items():
                if key == "name":
                    continue
                for ch in value:
                    with self.subTest(mode=mode, key=key, ch=hex(ord(ch))):
                        self.assertNotIn(
                            unicodedata.east_asian_width(ch), ("W", "F"),
                            f"{mode}.{key} contains wide char {ch!r}")

    def test_ascii_set_is_actually_ascii(self):
        glyphs = load(REPO / "glyphs" / "ascii.glyphs")
        for key, value in glyphs.items():
            if key == "name":
                continue
            with self.subTest(key=key):
                self.assertTrue(value.isascii(), f"ascii.{key}={value!r}")


class TestNoStaleArtifacts(unittest.TestCase):
    def test_generated_theme_confs_are_gone(self):
        """themes/*.conf were dead artifacts nothing ever read."""
        self.assertEqual(list((REPO / "themes").glob("*.conf")), [])

    def test_v1_modules_are_deleted(self):
        for gone in ("cli/config.py", "cli/generator.py",
                     "scripts/gen_themes.py", "scripts/status-right.sh",
                     "src/mac-cpu-pct.c"):
            with self.subTest(path=gone):
                self.assertFalse((REPO / gone).exists(),
                                 f"{gone} should have been deleted in the v2 cutover")


if __name__ == "__main__":
    unittest.main()
