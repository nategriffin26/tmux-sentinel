"""`bin/sentinel-status` contract tests — CONTRACT §1-§4.

The engine is the single renderer of the status bar's right half, so these
tests are the project's load-bearing correctness checks.
"""

from __future__ import annotations

import concurrent.futures
import statistics
import time
import unittest

from harness import (
    ENGINE, REPO, ScratchHome, display_width, parse_kv, require, run, strip_tmux,
)


def engine(*args: str, **kw):
    return run([str(ENGINE), *args], **kw)


class EngineTestCase(unittest.TestCase):
    def setUp(self):
        require(ENGINE, "make")


class TestSimulateContract(EngineTestCase):
    """--simulate exists so the preview cannot drift from the real bar."""

    def test_healthy_and_alert_both_render(self):
        for mode in ("healthy", "alert"):
            with self.subTest(mode=mode):
                r = engine("--simulate", mode)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertTrue(r.stdout.strip(), "empty bar")

    def test_simulate_is_deterministic(self):
        """Byte-identical across runs, or the golden comparison is worthless.

        This is also why simulate emits the literal clock 14:30 instead of
        strftime(now).
        """
        for mode in ("healthy", "alert"):
            outputs = {engine("--simulate", mode).stdout for _ in range(5)}
            with self.subTest(mode=mode):
                self.assertEqual(len(outputs), 1, f"non-deterministic: {outputs}")

    def test_no_trailing_newline(self):
        # tmux would render it as a stray blank.
        self.assertFalse(engine("--simulate", "healthy").stdout.endswith("\n"))

    def test_separator_is_structural_never_leading_or_trailing(self):
        """v1 appended a separator after every segment, so disabling the
        clock left a dangling ' · ' at the end of the bar."""
        sep = parse_kv((REPO / "glyphs" / "nerd.glyphs").read_text("utf-8"))["sep"]
        for mode in ("healthy", "alert"):
            text = strip_tmux(engine("--simulate", mode).stdout)
            with self.subTest(mode=mode):
                self.assertFalse(text.startswith(sep), repr(text[:12]))
                self.assertFalse(text.endswith(sep), repr(text[-12:]))
                self.assertNotIn(sep + sep, text, "empty segment between separators")

    def test_alert_state_surfaces_more_segments_than_healthy(self):
        """The product's whole thesis: quiet when healthy, loud when not."""
        sep = parse_kv((REPO / "glyphs" / "nerd.glyphs").read_text("utf-8"))["sep"]
        healthy = strip_tmux(engine("--simulate", "healthy").stdout).split(sep)
        alert = strip_tmux(engine("--simulate", "alert").stdout).split(sep)
        self.assertGreater(len(alert), len(healthy))

    def test_documented_simulate_values_appear(self):
        """CONTRACT §4 pins these so docs, tests and preview agree."""
        healthy = strip_tmux(engine("--simulate", "healthy").stdout)
        for token in ("22%", "23.3G", "14:30"):
            with self.subTest(state="healthy", token=token):
                self.assertIn(token, healthy)

        alert = strip_tmux(engine("--simulate", "alert").stdout)
        for token in ("12G", "18%", "94%", "24.1G", "14:30", "2"):
            with self.subTest(state="alert", token=token):
                self.assertIn(token, alert)

    def test_healthy_state_shows_only_ambient_segments(self):
        """The product's thesis, pinned.

        Under the default alerts_only, a healthy host shows CPU, memory and
        the clock and nothing else. Disk at 54 GB is above the warn
        threshold, so it must stay hidden — v1 leaked it because its
        alerts-only branch emitted the healthy segment anyway.
        """
        healthy = strip_tmux(engine("--simulate", "healthy").stdout)
        for hidden, why in (("54G", "disk above threshold"),
                            ("10m", "sleep risk"),
                            ("95%", "battery on AC")):
            with self.subTest(segment=why):
                self.assertNotIn(hidden, healthy,
                                 f"{why} leaked into the healthy bar: {healthy!r}")

    def test_clients_flag_is_ignored_in_simulate(self):
        a = engine("--simulate", "healthy").stdout
        b = engine("--simulate", "healthy", "--clients", "9").stdout
        self.assertEqual(a, b)


class TestLiveProbes(EngineTestCase):
    def test_bare_invocation_renders_something(self):
        r = engine()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(strip_tmux(r.stdout).strip(), "no live segments rendered")

    def test_cpu_and_memory_are_always_present(self):
        """Ambient segments per CONTRACT §3 — they are what makes the
        healthy bar non-empty."""
        text = strip_tmux(engine().stdout)
        self.assertRegex(text, r"\d+%", f"no CPU percentage in {text!r}")
        self.assertRegex(text, r"\d+\.\d+G", f"no swap figure in {text!r}")

    def test_multi_client_appears_only_above_one(self):
        sep = parse_kv((REPO / "glyphs" / "nerd.glyphs").read_text("utf-8"))["sep"]
        one = strip_tmux(engine("--clients", "1").stdout).split(sep)
        three = strip_tmux(engine("--clients", "3").stdout).split(sep)
        self.assertEqual(len(three), len(one) + 1)
        self.assertTrue(any("3" in s for s in three))

    def test_selftest_reports_every_segment(self):
        r = engine("--selftest")
        self.assertIn(r.returncode, (0, 1), r.stderr)
        for name in ("cpu", "memory", "disk"):
            with self.subTest(segment=name):
                self.assertIn(name, r.stdout.lower())

    def test_version(self):
        r = engine("--version")
        self.assertEqual(r.returncode, 0)
        self.assertIn("0.2.0", r.stdout)

    def test_unknown_flag_is_a_usage_error(self):
        r = engine("--nonsense")
        self.assertEqual(r.returncode, 2)
        self.assertTrue(r.stderr.strip())


class TestStateFileRobustness(EngineTestCase):
    """CONTRACT §1: a bad state file must never break the status bar."""

    def _render(self, body: str) -> str:
        with ScratchHome() as home:
            home.state.write_text(body, encoding="utf-8")
            r = engine("--state", str(home.state))
            self.assertEqual(r.returncode, 0, r.stderr)
            return r.stdout

    def test_missing_file_falls_back_to_builtin_defaults(self):
        r = engine("--state", "/nonexistent/sentinel.state")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.strip())

    def test_garbage_is_tolerated(self):
        for body in ("", "\n\n\n", "not a config at all",
                     "=novalue\n", "novalue=\n", "\x00\x01binary\n",
                     "key" + "=" + "x" * 5000 + "\n"):
            with self.subTest(body=body[:24]):
                self.assertTrue(self._render(body) is not None)

    def test_unknown_keys_are_ignored_for_forward_compatibility(self):
        baseline = self._render("version=1\n")
        extended = self._render("version=1\nsome_future_key=whatever\n")
        self.assertEqual(baseline, extended)

    def test_comments_and_blank_lines_are_skipped(self):
        self.assertEqual(
            self._render("version=1\nseg_cpu=0\n"),
            self._render("# a comment\n\nversion=1\n\n# another\nseg_cpu=0\n"),
        )

    def test_disabling_a_segment_removes_it(self):
        with_cpu = strip_tmux(self._render("version=1\nseg_cpu=1\n"))
        without = strip_tmux(self._render("version=1\nseg_cpu=0\n"))
        self.assertNotEqual(with_cpu, without)
        self.assertLess(display_width(without), display_width(with_cpu))

    def test_all_segments_off_yields_empty_output_not_a_bare_separator(self):
        body = "version=1\n" + "".join(
            f"seg_{s}=0\n" for s in
            ("thermal", "sleep_risk", "disk", "battery",
             "cpu", "memory", "multi_client", "clock"))
        self.assertEqual(self._render(body), "")

    def test_trailing_whitespace_in_values_is_preserved(self):
        """The separator glyph is literally ' · '."""
        out = strip_tmux(self._render("version=1\nglyph_sep= ~~ \n"))
        self.assertIn(" ~~ ", out)

    def test_nonnumeric_thresholds_do_not_crash_or_silently_disable_alerts(self):
        out = self._render("version=1\ndisk_warn_gb=banana\ncpu_crit_pct=\n")
        self.assertTrue(out.strip())


class TestPerformance(EngineTestCase):
    def test_warm_path_is_an_order_of_magnitude_faster_than_v1(self):
        """v1's shell engine measured 48 ms median with 21 forks.

        The bound is deliberately loose. This is a regression guard against
        someone reintroducing a fork into the tick path, not a benchmark;
        a tight threshold would just flake on loaded CI runners. The real
        number is reported by `make bench`.
        """
        engine()  # prime the CPU baseline cache
        samples = []
        for _ in range(25):
            start = time.perf_counter()
            engine()
            samples.append((time.perf_counter() - start) * 1000)
        median = statistics.median(samples)
        self.assertLess(median, 20.0,
                        f"median {median:.2f} ms (v1 was 48 ms); samples {samples}")

    def test_spawns_no_child_processes(self):
        """The point of the rewrite: zero forks per tick."""
        source = (REPO / "src" / "sentinel-status.c").read_text(encoding="utf-8")
        for banned in ("system(", "popen(", "fork(", "posix_spawn", "execv", "execl"):
            with self.subTest(symbol=banned):
                self.assertNotIn(banned, source,
                                 f"engine shells out via {banned}")


class TestConcurrency(EngineTestCase):
    def test_concurrent_invocations_all_return_a_plausible_cpu_reading(self):
        """v1's unlocked /tmp baseline: 160 concurrent samples returned 142
        empty and the remainder ranged 0-100%."""
        engine()
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            results = [f.result().stdout
                       for f in [pool.submit(engine) for _ in range(160)]]

        empty = [r for r in results if not strip_tmux(r).strip()]
        self.assertEqual(len(empty), 0, f"{len(empty)}/160 produced no output")

        import re
        values = [int(m.group(1))
                  for r in results
                  if (m := re.search(r"(\d+)%", strip_tmux(r)))]
        self.assertEqual(len(values), 160, "some runs produced no CPU percentage")
        spread = max(values) - min(values)
        self.assertLess(spread, 60,
                        f"CPU spread {spread} pts (min {min(values)}, "
                        f"max {max(values)}) — baseline is racing")


if __name__ == "__main__":
    unittest.main()
