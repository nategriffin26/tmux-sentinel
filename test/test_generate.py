"""`scripts/generate.sh` contract tests — CONTRACT §1, §5, §7, §10.

generate.sh is the project's only generator and its only security boundary:
every user-controlled value reaches tmux config through it.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import unittest
from pathlib import Path

from harness import (
    GENERATE, GLYPH_MODES, REPO, ScratchHome, THEMES, TmuxServer, parse_kv,
    require, run,
)

SEGMENTS = ("thermal", "sleep_risk", "disk", "battery",
            "cpu", "memory", "multi_client", "clock")

# Segments that render even with nothing to report, out of the box. `disk` is
# here because free space is the one figure people want a resting readout of.
DEFAULT_ALWAYS = {"disk", "cpu", "memory", "clock"}

REQUIRED_STATE_KEYS = {
    "version", "clock_format",
    "color_fg", "color_dim", "color_val", "color_sep",
    "color_alert", "color_warn", "color_peach", "color_info",
    "glyph_sep", "glyph_thermal", "glyph_sleep", "glyph_disk",
    "glyph_battery_full", "glyph_battery_mid", "glyph_battery_low",
    "glyph_cpu", "glyph_memory", "glyph_clients",
    "disk_warn_gb", "disk_crit_gb", "cpu_warn_pct", "cpu_crit_pct",
    "battery_warn_pct", "battery_crit_pct",
} | {f"seg_{s}" for s in SEGMENTS} | {f"always_{s}" for s in SEGMENTS}


class GenerateTestCase(unittest.TestCase):
    def setUp(self):
        require(GENERATE, "git checkout")

    def generate(self, server: TmuxServer, home: ScratchHome, **options):
        for key, value in options.items():
            server.set_option(f"@sentinel_{key}", value)
        env = home.env()
        env["TMUX_SENTINEL_SOCKET"] = server.socket
        r = run([str(GENERATE)], env=env,
                cwd=str(REPO),
                # generate.sh talks to the server it was invoked under
                )
        return r


class TestStateGrammar(GenerateTestCase):
    def test_state_contains_every_contracted_key(self):
        with TmuxServer() as server, ScratchHome() as home:
            self.generate(server, home)
            state = parse_kv(home.state.read_text(encoding="utf-8"))
            missing = REQUIRED_STATE_KEYS - set(state)
            self.assertFalse(missing, f"missing state keys: {sorted(missing)}")

    def test_separator_padding_survives_the_round_trip(self):
        with TmuxServer() as server, ScratchHome() as home:
            self.generate(server, home, glyphs="nerd")
            state = parse_kv(home.state.read_text(encoding="utf-8"))
            self.assertEqual(state["glyph_sep"], " · ", repr(state["glyph_sep"]))

    def test_colours_come_from_the_selected_palette(self):
        with TmuxServer() as server, ScratchHome() as home:
            self.generate(server, home, theme="nord")
            state = parse_kv(home.state.read_text(encoding="utf-8"))
            palette = parse_kv((REPO / "themes" / "nord.palette").read_text("utf-8"))
            self.assertEqual(state["color_alert"], palette["alert"])
            self.assertEqual(state["color_dim"], palette["dim"])

    def test_state_has_no_shell_syntax(self):
        """v2 parses the state file; it must never be shell-sourced again.

        v1 sourced env.sh every tick, which is what made the injection a
        repeating RCE.
        """
        with TmuxServer() as server, ScratchHome() as home:
            self.generate(server, home)
            body = home.state.read_text(encoding="utf-8")
            self.assertNotIn("export ", body)
            self.assertNotIn("$(", body)
            self.assertNotIn("`", body)


class TestAlwaysFlags(GenerateTestCase):
    """`@sentinel_always` replaced the global alerts_only boolean in 0.3.0."""

    def _always(self, server, home, value=None):
        opts = {"always": value} if value is not None else {}
        self.generate(server, home, **opts)
        state = parse_kv(home.state.read_text(encoding="utf-8"))
        return {s for s in SEGMENTS if state.get(f"always_{s}") == "1"}

    def test_disk_is_visible_out_of_the_box(self):
        """The headline of this release: free space needs no configuration."""
        with TmuxServer() as server, ScratchHome() as home:
            self.assertIn("disk", self._always(server, home))

    def test_default_matches_the_documented_set(self):
        with TmuxServer() as server, ScratchHome() as home:
            self.assertEqual(self._always(server, home), DEFAULT_ALWAYS)

    def test_the_list_drives_the_flags(self):
        with TmuxServer() as server, ScratchHome() as home:
            for value, expected in (
                ("disk", {"disk"}),
                ("thermal,battery", {"thermal", "battery"}),
                (",".join(SEGMENTS), set(SEGMENTS)),
            ):
                with self.subTest(always=value):
                    self.assertEqual(self._always(server, home, value), expected)

    def test_every_segment_can_be_given_a_resting_state(self):
        """'for all options' — no segment is hardcoded out of the control."""
        with TmuxServer() as server, ScratchHome() as home:
            for segment in SEGMENTS:
                with self.subTest(segment=segment):
                    self.assertIn(segment, self._always(server, home, segment))

    def test_malformed_lists_fall_back_to_the_default(self):
        with TmuxServer() as server, ScratchHome() as home:
            for bad in ("disk, cpu", "nonsense", "disk,nonsense", "", "disk;cpu"):
                with self.subTest(always=bad):
                    self.assertEqual(self._always(server, home, bad), DEFAULT_ALWAYS)

    def test_removed_option_is_not_silently_ignored(self):
        """Dropping a setting the user wrote without a word is the exact
        'knob that lies' defect this project already fixed once."""
        with TmuxServer() as server, ScratchHome() as home:
            server.set_option("@sentinel_alerts_only", "off")
            result = self.generate(server, home)
            message = (result.stdout + result.stderr).lower()
            self.assertIn("alerts_only", message)
            self.assertIn("always", message)

    def test_alerts_only_no_longer_appears_in_generated_state(self):
        with TmuxServer() as server, ScratchHome() as home:
            self.generate(server, home)
            self.assertNotIn("alerts_only",
                             home.state.read_text(encoding="utf-8"))


class TestTmuxAcceptsEveryGeneratedConfig(GenerateTestCase):
    def test_full_option_matrix_sources_cleanly(self):
        """The highest-value test in the project.

        v1 shipped `status-position middle` and `status-interval -5`, both
        of which tmux rejects outright, and nothing noticed.
        """
        failures = []
        checked = 0
        with TmuxServer() as server, ScratchHome() as home:
            for theme in THEMES:
                for position in ("top", "bottom"):
                    for windows in ("hidden", "minimal", "tabs"):
                        self.generate(server, home, theme=theme,
                                      position=position, windows=windows)
                        r = server.source(home.conf)
                        checked += 1
                        if r.returncode != 0 or r.stderr.strip():
                            failures.append(
                                f"{theme}/{position}/{windows}: "
                                f"rc={r.returncode} {r.stderr.strip()}")
        self.assertEqual(checked, len(THEMES) * 2 * 3)
        self.assertEqual(failures, [], "\n".join(failures))

    def test_every_glyph_mode_sources_cleanly(self):
        with TmuxServer() as server, ScratchHome() as home:
            for mode in GLYPH_MODES:
                with self.subTest(glyphs=mode):
                    self.generate(server, home, glyphs=mode)
                    r = server.source(home.conf)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertFalse(r.stderr.strip())


class TestInjectionIsRejected(GenerateTestCase):
    """v1 had a confirmed, reproduced RCE here. These are its regression tests."""

    PAYLOADS = {
        "clock_format": '%H:%M"$(touch {marker})"',
        "accent": 'X"; run-shell \'touch {marker}\' ; set -g status-left "Y',
        "disk_warn_gb": '25"; touch {marker}; #',
        "theme": "../../../etc/passwd",
        "glyphs": "../../etc/hosts",
        "position": "middle",
        "interval": "-5",
        "session_max_length": "999999",
    }

    def test_hostile_option_values_never_execute_and_never_break_the_config(self):
        with TmuxServer() as server, ScratchHome() as home:
            markers = []
            for option, template in self.PAYLOADS.items():
                marker = home.root / f"PWNED_{option}"
                markers.append(marker)
                server.set_option(f"@sentinel_{option}",
                                  template.format(marker=marker))

            self.generate(server, home)

            # 1. Nothing executed during generation.
            for marker in markers:
                self.assertFalse(marker.exists(),
                                 f"payload executed: {marker.name}")

            # 2. tmux still accepts the result.
            r = server.source(home.conf)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertFalse(r.stderr.strip(), r.stderr)

            # 3. Nothing executed while tmux sourced it.
            for marker in markers:
                self.assertFalse(marker.exists(),
                                 f"payload executed via tmux: {marker.name}")

            # 4. Out-of-domain values fell back to their defaults.
            self.assertEqual(server.option("status-position"), "top")
            self.assertGreaterEqual(int(server.option("status-interval")), 1)

    def test_newline_in_a_value_cannot_inject_a_tmux_command(self):
        """A \\n in clock_format escaped the line in v1 and injected
        `set -g status-position bottom`."""
        with TmuxServer() as server, ScratchHome() as home:
            server.set_option("@sentinel_clock_format",
                              '%H:%M"\nset -g status-position bottom\n')
            self.generate(server, home)
            server.source(home.conf)
            self.assertEqual(server.option("status-position"), "top")

    def test_state_file_never_contains_a_newline_injected_value(self):
        with TmuxServer() as server, ScratchHome() as home:
            server.set_option("@sentinel_clock_format", "%H:%M\nseg_cpu=0")
            self.generate(server, home)
            state = parse_kv(home.state.read_text(encoding="utf-8"))
            self.assertEqual(state.get("seg_cpu"), "1",
                             "newline in a value forged a second state key")


class TestPathsSelfHeal(GenerateTestCase):
    def test_moving_the_repo_repoints_status_right(self):
        """v1 baked an absolute path at install time; moving the checkout
        silently emptied the whole health half of the bar."""
        with ScratchHome() as home, TmuxServer() as server:
            repo_a = home.root / "repoA"
            shutil.copytree(REPO, repo_a,
                            ignore=shutil.ignore_patterns(".git", "__pycache__"))
            run([str(repo_a / "scripts" / "generate.sh")], env=home.env(),
                cwd=str(repo_a))
            self.assertIn("repoA", home.conf.read_text(encoding="utf-8"))

            repo_b = home.root / "repoB"
            repo_a.rename(repo_b)
            run([str(repo_b / "scripts" / "generate.sh")], env=home.env(),
                cwd=str(repo_b))
            conf = home.conf.read_text(encoding="utf-8")
            self.assertIn("repoB", conf)
            self.assertNotIn("repoA", conf)

    def test_status_right_points_at_an_executable_that_exists(self):
        with TmuxServer() as server, ScratchHome() as home:
            self.generate(server, home)
            conf = home.conf.read_text(encoding="utf-8")
            match = re.search(r"status-right\s+\"#\((\S+)", conf)
            self.assertIsNotNone(match, conf)
            command = Path(match.group(1).strip("'\""))
            self.assertTrue(command.exists(), f"{command} does not exist")
            self.assertTrue(os.access(command, os.X_OK), f"{command} not executable")


class TestWritesAreAtomic(GenerateTestCase):
    def test_no_temp_files_left_behind(self):
        with TmuxServer() as server, ScratchHome() as home:
            self.generate(server, home)
            leftovers = [p.name for p in home.config_dir.iterdir()
                         if p.name.startswith(".") or p.suffix in (".tmp", ".new")]
            self.assertEqual(leftovers, [])

    def test_env_sh_is_never_produced(self):
        """The v1 artifact that made the injection a per-tick RCE."""
        with TmuxServer() as server, ScratchHome() as home:
            self.generate(server, home)
            self.assertFalse((home.config_dir / "env.sh").exists())


if __name__ == "__main__":
    unittest.main()
