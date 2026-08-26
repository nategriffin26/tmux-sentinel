"""`bin/sentinel` CLI and preview tests.

Python no longer renders the bar in v2 — it drives tmux options and converts
the engine's output for preview — so these tests target dispatch, validation,
error propagation and column arithmetic.
"""

from __future__ import annotations

import shutil
import subprocess
import unittest

from harness import (
    VERSION,
    ENGINE, LAUNCHER, REPO, ScratchHome, THEMES, TmuxServer, display_width,
    require, run, strip_ansi,
)


def sentinel(*args: str, home: ScratchHome, server: TmuxServer | None = None,
             **kw) -> subprocess.CompletedProcess:
    env = home.env()
    if server is not None:
        env["TMUX_SENTINEL_SOCKET"] = server.socket
    kw.setdefault("env", env)
    kw.setdefault("cwd", str(REPO))
    return run([str(LAUNCHER), *args], **kw)


class CliTestCase(unittest.TestCase):
    def setUp(self):
        require(LAUNCHER, "git checkout")


class TestDispatch(CliTestCase):
    def test_version(self):
        with ScratchHome() as home:
            r = sentinel("--version", home=home)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn(VERSION, r.stdout)

    def test_bare_invocation_on_a_pipe_does_not_crash(self):
        """v1: AttributeError: 'Namespace' object has no attribute 'theme'.

        Hit by `sentinel | less`, prompt hooks, run-shell and CI.
        """
        with ScratchHome() as home:
            r = sentinel(home=home, stdin=subprocess.DEVNULL)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertNotIn("Traceback", r.stderr)

    def test_no_subcommand_traceback_on_any_read_only_command(self):
        read_only = (["preview"], ["theme"], ["get"], ["doctor"], ["--help"])
        with ScratchHome() as home:
            for argv in read_only:
                with self.subTest(argv=argv):
                    r = sentinel(*argv, home=home, stdin=subprocess.DEVNULL)
                    self.assertNotIn("Traceback", r.stderr, r.stderr)

    def test_unknown_subcommand_fails_cleanly(self):
        with ScratchHome() as home:
            r = sentinel("nonsense", home=home)
            self.assertNotEqual(r.returncode, 0)
            self.assertNotIn("Traceback", r.stderr)


class TestValidation(CliTestCase):
    """v1 accepted anything int()-parseable and reported success while tmux
    answered `value is too small: -5`."""

    REJECTED = [
        ("set", "interval", "-5"),
        ("set", "interval", "0"),
        ("set", "position", "middle"),
        ("set", "glyphs", "emoji"),
        ("set", "cpu_warn_pct", "500"),
        ("set", "always", "disk, cpu"),
        ("set", "always", "nonsense"),
        ("set", "always", "disk,nonsense"),
        ("theme", "no-such-theme"),
        ("toggle", "no-such-segment"),
        ("always", "no-such-segment"),
    ]

    def test_out_of_domain_values_are_rejected(self):
        with ScratchHome() as home, TmuxServer() as server:
            for argv in self.REJECTED:
                with self.subTest(argv=argv):
                    r = sentinel(*argv, home=home, server=server)
                    self.assertNotEqual(r.returncode, 0,
                                        f"accepted {argv}: {r.stdout}")

    def test_diagnostics_go_to_stderr_not_stdout(self):
        """`sentinel get theme 2>/dev/null` must emit only the value."""
        with ScratchHome() as home, TmuxServer() as server:
            r = sentinel("theme", "no-such-theme", home=home, server=server)
            self.assertTrue(r.stderr.strip(), "error message went to stdout")

    def test_thresholds_are_reachable_from_the_cli(self):
        """In v1 they existed only in config.json, so hand-editing JSON was
        the documented workflow — which was the injection vector."""
        with ScratchHome() as home, TmuxServer() as server:
            for key, value in (("disk_warn_gb", "30"), ("cpu_warn_pct", "65"),
                               ("battery_crit_pct", "15")):
                with self.subTest(key=key):
                    r = sentinel("set", key, value, home=home, server=server)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertEqual(
                        server.option(f"@sentinel_{key}"), value)

    def test_every_bundled_theme_is_accepted(self):
        with ScratchHome() as home, TmuxServer() as server:
            for theme in THEMES:
                with self.subTest(theme=theme):
                    r = sentinel("theme", theme, home=home, server=server)
                    self.assertEqual(r.returncode, 0, r.stderr)


class TestAlwaysVerb(CliTestCase):
    """`@sentinel_always` replaced the global alerts_only boolean in 0.3.0."""

    def _always(self, home, server):
        """The *resolved* set, not the raw tmux option.

        An unset option is meant to stay unset — defaults are applied at
        read time rather than written onto the server — so the raw option
        is empty until something changes it.
        """
        r = sentinel("get", "always", home=home, server=server)
        self.assertEqual(r.returncode, 0, r.stderr)
        return {s for s in r.stdout.strip().split(",") if s}

    def test_disk_is_in_the_default_set(self):
        with ScratchHome() as home, TmuxServer() as server:
            self.assertIn("disk", self._always(home, server))

    def test_verb_flips_one_segment_both_ways(self):
        """Typing a whole comma list to turn one thing on is the papercut
        `toggle` already exists to avoid."""
        with ScratchHome() as home, TmuxServer() as server:
            before = self._always(home, server)
            self.assertNotIn("battery", before)

            r = sentinel("always", "battery", home=home, server=server)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self._always(home, server), before | {"battery"})

            r = sentinel("always", "battery", home=home, server=server)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(self._always(home, server), before)

    def test_every_segment_is_reachable(self):
        """'for all options' — nothing is hardcoded out of the control."""
        with ScratchHome() as home, TmuxServer() as server:
            sentinel("apply", home=home, server=server)
            for segment in ("thermal", "sleep_risk", "disk", "battery",
                            "cpu", "memory", "multi_client", "clock"):
                with self.subTest(segment=segment):
                    r = sentinel("always", segment, home=home, server=server)
                    self.assertEqual(r.returncode, 0, r.stderr)

    def test_preview_reflects_the_setting(self):
        """The preview renders the engine's bytes, so a config change the
        preview cannot show would mean the two had drifted apart."""
        require(ENGINE, "make")
        with ScratchHome() as home, TmuxServer() as server:
            sentinel("apply", home=home, server=server)
            with_disk = sentinel("preview", home=home, server=server).stdout
            self.assertIn("54G", strip_ansi(with_disk))

            sentinel("always", "disk", home=home, server=server)
            without = sentinel("preview", home=home, server=server).stdout
            self.assertNotIn("54G", strip_ansi(without))

    def test_removed_option_is_gone_from_the_cli(self):
        with ScratchHome() as home, TmuxServer() as server:
            r = sentinel("set", "alerts_only", "off", home=home, server=server)
            self.assertNotEqual(r.returncode, 0)


    def test_a_v0_2_options_file_still_works_after_upgrade(self):
        """A removed option must not brick the CLI for anyone upgrading.

        The first cut aborted with "unknown option @sentinel_alerts_only",
        which meant every 0.2.0 user's `sentinel` stopped working the
        moment they pulled.
        """
        with ScratchHome() as home, TmuxServer() as server:
            (home.config_dir / "options.conf").write_text(
                '# tmux-sentinel persisted options\n'
                'set -ogq @sentinel_theme "nord"\n'
                'set -ogq @sentinel_alerts_only "on"\n',
                encoding="utf-8")

            r = sentinel("get", "theme", home=home, server=server)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout.strip(), "nord",
                             "surviving options must still be honoured")
            self.assertIn("alerts_only", r.stderr)
            self.assertIn("always", r.stderr,
                          "the warning must name the replacement")

    def test_a_genuinely_unknown_option_still_aborts(self):
        """Tolerating removed keys must not become tolerating typos."""
        with ScratchHome() as home, TmuxServer() as server:
            (home.config_dir / "options.conf").write_text(
                'set -ogq @sentinel_thmee "nord"\n', encoding="utf-8")
            r = sentinel("get", "theme", home=home, server=server)
            self.assertNotEqual(r.returncode, 0)
            self.assertIn("thmee", r.stderr)


class TestOptionsPersistence(CliTestCase):
    def test_changes_reach_both_the_server_and_options_conf(self):
        with ScratchHome() as home, TmuxServer() as server:
            sentinel("theme", "nord", home=home, server=server)
            self.assertEqual(server.option("@sentinel_theme"), "nord")
            persisted = (home.config_dir / "options.conf").read_text("utf-8")
            self.assertIn("@sentinel_theme", persisted)
            self.assertIn("nord", persisted)

    def test_persisted_options_use_set_o_so_tmux_conf_wins(self):
        """CONTRACT §6: an explicit `set -g` in .tmux.conf must beat the
        TUI's saved state."""
        with ScratchHome() as home, TmuxServer() as server:
            sentinel("theme", "nord", home=home, server=server)
            persisted = (home.config_dir / "options.conf").read_text("utf-8")
            for line in persisted.splitlines():
                if "@sentinel_" in line:
                    self.assertRegex(line, r"set\s+-\S*o\S*\s",
                                     f"not only-if-unset: {line!r}")

    def test_a_corrupt_options_file_aborts_instead_of_silently_resetting(self):
        """v1 swallowed a malformed config, reported it healthy, and then
        overwrote the user's file with defaults on the next mutation."""
        with ScratchHome() as home, TmuxServer() as server:
            corrupt = home.config_dir / "options.conf"
            corrupt.write_text("set -ogq @sentinel_theme \"nord\nunterminated",
                               encoding="utf-8")
            before = corrupt.read_bytes()
            r = sentinel("get", home=home, server=server)
            if r.returncode == 0:
                self.assertEqual(corrupt.read_bytes(), before,
                                 "user's file was rewritten")
            else:
                self.assertTrue(r.stderr.strip())


class TestPreviewWidth(CliTestCase):
    """v1 measured padding with len(): +0 nerd, +2 unicode, +4 ascii, so the
    preview wrapped onto a second line."""

    def setUp(self):
        super().setUp()
        require(ENGINE, "make")

    def test_preview_never_exceeds_the_requested_width(self):
        with ScratchHome() as home, TmuxServer() as server:
            for mode in ("nerd", "unicode", "ascii"):
                sentinel("set", "glyphs", mode, home=home, server=server)
                for width in (80, 100, 120):
                    r = sentinel("preview", "--width", str(width),
                                 home=home, server=server)
                    with self.subTest(glyphs=mode, width=width):
                        self.assertEqual(r.returncode, 0, r.stderr)
                        for line in r.stdout.splitlines():
                            text = strip_ansi(line)
                            if not text.strip():
                                continue
                            self.assertLessEqual(
                                display_width(text), width,
                                f"{mode}@{width}: {display_width(text)} cols")


class TestLauncherGuard(CliTestCase):
    def test_a_detached_copy_fails_with_an_actionable_message(self):
        """v1's `make install-bin` cp'd the launcher; the copy died with a
        bare ModuleNotFoundError."""
        with ScratchHome() as home:
            elsewhere = home.root / "bin"
            elsewhere.mkdir(parents=True, exist_ok=True)
            shutil.copy2(LAUNCHER, elsewhere / "sentinel")
            r = run([str(elsewhere / "sentinel"), "--version"])
            self.assertNotIn("ModuleNotFoundError", r.stderr)
            self.assertNotIn("Traceback", r.stderr)
            if r.returncode != 0:
                self.assertIn("symlink", (r.stderr + r.stdout).lower())

    def test_a_symlink_works(self):
        with ScratchHome() as home:
            link = home.root / "sentinel"
            link.symlink_to(LAUNCHER)
            r = run([str(link), "--version"], env=home.env())
            self.assertEqual(r.returncode, 0, r.stderr)


class TestDoctor(CliTestCase):
    def test_doctor_goes_red_when_the_config_is_missing(self):
        """v1's doctor reported green on every failure mode in the audit."""
        with ScratchHome() as home, TmuxServer() as server:
            sentinel("apply", home=home, server=server)
            home.conf.unlink(missing_ok=True)
            r = sentinel("doctor", home=home, server=server)
            combined = (r.stdout + r.stderr).lower()
            self.assertTrue(
                r.returncode != 0 or "✗" in combined or "fail" in combined
                or "missing" in combined,
                f"doctor stayed green with no config:\n{r.stdout}")

    def test_doctor_reports_engine_selftest(self):
        require(ENGINE, "make")
        with ScratchHome() as home, TmuxServer() as server:
            sentinel("apply", home=home, server=server)
            r = sentinel("doctor", home=home, server=server)
            self.assertIn("cpu", (r.stdout + r.stderr).lower())


if __name__ == "__main__":
    unittest.main()
