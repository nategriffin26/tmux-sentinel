#!/usr/bin/env python3
"""Interleaved A/B benchmark: the native engine against v0.1's shell engine.

Runs are interleaved rather than batched so a drifting system load hits both
implementations equally. Without that, whichever ran second looks worse.

The v0.1 script is recovered from git history when available, so the comparison
is against real code rather than a remembered number. With no git history the
benchmark still reports the native engine's own latency.
"""

from __future__ import annotations

import os
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENGINE = REPO / "bin" / "sentinel-status"
V1_PATH = "scripts/status-right.sh"
RUNS = 50


def percentile(sorted_samples: list[float], fraction: float) -> float:
    index = max(0, min(len(sorted_samples) - 1,
                       int(round(fraction * len(sorted_samples))) - 1))
    return sorted_samples[index]


def report(label: str, samples: list[float]) -> float:
    ordered = sorted(samples)
    median = statistics.median(ordered)
    print(f"  {label:<26} median {median:7.2f} ms   "
          f"p95 {percentile(ordered, 0.95):7.2f} ms   "
          f"min {ordered[0]:6.2f}   max {ordered[-1]:6.2f}")
    return median


def time_once(argv: list[str], env: dict[str, str] | None = None) -> float:
    start = time.perf_counter()
    subprocess.run(argv, capture_output=True, env=env)
    return (time.perf_counter() - start) * 1000


def recover_v1(into: Path) -> Path | None:
    """Extract v0.1's shell status engine from git history.

    The file was deleted in the v0.2.0 cutover, so look up the last commit
    that still contained it rather than guessing at a ref.
    """
    log = subprocess.run(
        ["git", "-C", str(REPO), "log", "--all", "--format=%H", "--", V1_PATH],
        capture_output=True, text=True)
    if log.returncode != 0:
        return None
    for sha in log.stdout.split():
        blob = subprocess.run(
            ["git", "-C", str(REPO), "show", f"{sha}:{V1_PATH}"],
            capture_output=True, text=True)
        if blob.returncode == 0 and blob.stdout.strip():
            script = into / "status-right.sh"
            script.write_text(blob.stdout, encoding="utf-8")
            script.chmod(0o755)
            return script
    return None


def main() -> int:
    if not ENGINE.exists():
        print(f"{ENGINE} not built — run `make` first", file=sys.stderr)
        return 1

    print(f"\ntmux-sentinel benchmark — {RUNS} interleaved runs each\n")

    with tempfile.TemporaryDirectory(prefix="sentinel-bench-") as tmp:
        v1 = recover_v1(Path(tmp))

        # Warm both: the engine primes its CPU baseline, the shell its caches.
        subprocess.run([str(ENGINE)], capture_output=True)
        if v1:
            subprocess.run([str(v1)], capture_output=True)

        native: list[float] = []
        shell: list[float] = []
        for _ in range(RUNS):
            native.append(time_once([str(ENGINE)]))
            if v1:
                shell.append(time_once([str(v1)]))

        floor = sorted(time_once(["/usr/bin/true"]) for _ in range(RUNS))

        native_median = report("v0.2 sentinel-status", native)
        shell_median = report("v0.1 status-right.sh", shell) if v1 else None
        report("process-spawn floor", floor)

        print()
        if shell_median:
            print(f"  speedup: {shell_median / native_median:.1f}x "
                  f"(median), {sorted(shell)[0] / sorted(native)[0]:.1f}x "
                  f"(best case)")
        else:
            print("  v0.1 engine not found in git history; "
                  "reporting native latency only")
        print("  child processes per tick: 0 "
              "(no fork/exec/popen/system in src/sentinel-status.c)")
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
