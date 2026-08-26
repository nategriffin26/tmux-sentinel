"""Shared test helpers for tmux-sentinel.

Stdlib only, on purpose: the suite must run in CI with nothing installed
beyond python3 and tmux.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
import unittest
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ENGINE = REPO / "bin" / "sentinel-status"
GENERATE = REPO / "scripts" / "generate.sh"
ENTRYPOINT = REPO / "sentinel.tmux"
LAUNCHER = REPO / "bin" / "sentinel"
THEMES = sorted(p.stem for p in (REPO / "themes").glob("*.palette"))
GLYPH_MODES = sorted(p.stem for p in (REPO / "glyphs").glob("*.glyphs"))

# Derived, never restated: a hardcoded copy here would drift the moment the
# project is versioned, and the tests exist to catch drift.
VERSION = re.search(
    r'__version__\s*=\s*"([^"]+)"',
    (REPO / "cli" / "__init__.py").read_text(encoding="utf-8"),
).group(1)

# CONTRACT §3 — the fixed segment order.
SEGMENT_ORDER = [
    "thermal", "sleep_risk", "disk", "battery",
    "cpu", "memory", "multi_client", "clock",
]

TMUX_TAG = re.compile(r"#\[[^\]]*\]")
ANSI = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")


def parse_kv(text: str) -> dict[str, str]:
    """CONTRACT §1 grammar. Trailing whitespace in values is significant."""
    out: dict[str, str] = {}
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        out[key] = value
    return out


def strip_tmux(s: str) -> str:
    return TMUX_TAG.sub("", s)


def strip_ansi(s: str) -> str:
    return ANSI.sub("", s)


def display_width(s: str) -> int:
    """Terminal column count, not codepoint count.

    v1 used len() and overflowed by +2 on the unicode glyph set (U+26A1,
    U+1F465) and +4 on ascii.
    """
    width = 0
    for ch in s:
        if unicodedata.combining(ch) or ch == "\ufe0f":
            continue
        width += 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1
    return width


def run(argv, **kw) -> subprocess.CompletedProcess:
    kw.setdefault("capture_output", True)
    kw.setdefault("text", True)
    kw.setdefault("timeout", 30)
    return subprocess.run(argv, **kw)


class TmuxServer:
    """A private tmux server on a throwaway socket.

    Never touches the user's session. Socket names are randomised so
    concurrent test runs cannot collide.
    """

    def __init__(self) -> None:
        self.socket = f"sent-test-{uuid.uuid4().hex[:12]}"

    def __enter__(self) -> "TmuxServer":
        run(self.cmd("new-session", "-d", "-s", "t", "-x", "200", "-y", "50"))
        return self

    def __exit__(self, *exc) -> None:
        run(self.cmd("kill-server"))

    def cmd(self, *args: str) -> list[str]:
        return ["tmux", "-L", self.socket, "-f", "/dev/null", *args]

    def __call__(self, *args: str) -> subprocess.CompletedProcess:
        return run(self.cmd(*args))

    def source(self, conf: Path) -> subprocess.CompletedProcess:
        return self("source-file", str(conf))

    def option(self, name: str) -> str:
        return self("show-option", "-gqv", name).stdout.strip()

    def set_option(self, name: str, value: str) -> None:
        r = self("set-option", "-g", name, value)
        assert r.returncode == 0, r.stderr


class ScratchHome:
    """Isolated XDG_CONFIG_HOME so tests never read or write the real config."""

    def __init__(self) -> None:
        self.root = Path(tempfile.mkdtemp(prefix="sentinel-test-"))

    def __enter__(self) -> "ScratchHome":
        (self.config_dir).mkdir(parents=True, exist_ok=True)
        return self

    def __exit__(self, *exc) -> None:
        shutil.rmtree(self.root, ignore_errors=True)

    @property
    def xdg(self) -> Path:
        return self.root / "config"

    @property
    def config_dir(self) -> Path:
        return self.xdg / "tmux-sentinel"

    @property
    def state(self) -> Path:
        return self.config_dir / "sentinel.state"

    @property
    def conf(self) -> Path:
        return self.config_dir / "sentinel.conf"

    def env(self, **extra: str) -> dict[str, str]:
        env = dict(os.environ)
        env["XDG_CONFIG_HOME"] = str(self.xdg)
        env["HOME"] = str(self.root)
        env.update(extra)
        return env


def require(path: Path, how: str) -> None:
    """Fail loudly rather than skipping: a missing component is a failure."""
    if not path.exists():
        raise unittest.SkipTest(f"{path.relative_to(REPO)} not built — run `{how}`")
