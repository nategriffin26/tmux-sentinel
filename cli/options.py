"""tmux-options layer: the single source of truth for tmux-sentinel settings.

Settings live in tmux server options named ``@sentinel_*`` (CONTRACT SS5).  This
module reads them from the live server, validates every value against its
documented domain, and persists mutations to
``$XDG_CONFIG_HOME/tmux-sentinel/options.conf`` as ``set -ogq`` lines.

There is no JSON config and no generated ``env.sh``.  Nothing here interpolates a
user-controlled value into a shell or tmux command line without domain
validation first.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence, Tuple

VERSION = "0.3.0"

REPO_ROOT = Path(__file__).resolve().parent.parent

CONFIG_DIR = Path(
    os.environ.get("XDG_CONFIG_HOME") or (Path.home() / ".config")
) / "tmux-sentinel"
OPTIONS_CONF = CONFIG_DIR / "options.conf"
SENTINEL_CONF = CONFIG_DIR / "sentinel.conf"
STATE_FILE = CONFIG_DIR / "sentinel.state"
LEGACY_JSON = CONFIG_DIR / "config.json"

GENERATE_SH = REPO_ROOT / "scripts" / "generate.sh"
STATUS_BIN = REPO_ROOT / "bin" / "sentinel-status"
FALLBACK_SH = REPO_ROOT / "scripts" / "status-fallback.sh"

OPT_PREFIX = "@sentinel_"

#: Segment identifiers, in the fixed render order of CONTRACT SS3.
SEGMENTS: Tuple[str, ...] = (
    "thermal",
    "sleep_risk",
    "disk",
    "battery",
    "cpu",
    "memory",
    "multi_client",
    "clock",
)

#: Segments with no quiet state: they render unconditionally, so their
#: ``always_*`` state key is accepted but inert (CONTRACT SS3).
INERT_ALWAYS: Tuple[str, ...] = ("cpu", "memory", "clock")

#: Shipped resting-state set for ``@sentinel_always`` (CONTRACT SS5).
ALWAYS_DEFAULT = "disk,cpu,memory,clock"

GLYPH_MODES: Tuple[str, ...] = ("nerd", "unicode", "ascii")
POSITIONS: Tuple[str, ...] = ("top", "bottom")
WINDOW_MODES: Tuple[str, ...] = ("hidden", "minimal", "tabs")

CLOCK_FORMAT_RE = re.compile(r"^[%A-Za-z0-9 :/.,+-]{1,32}$")
ACCENT_FORBIDDEN = set('"\'\\$;#\n\r')

_OPTIONS_LINE_RE = re.compile(
    r'^set(?:-option)?[ \t]+-ogq[ \t]+@sentinel_([a-z0-9_]+)[ \t]+"([^"]*)"[ \t]*$'
)


class OptionError(Exception):
    """A caller-supplied value is outside its documented domain."""


class ConfigError(Exception):
    """The on-disk options.conf is unreadable or malformed; abort loudly."""


def warn(msg: str) -> None:
    """Emit a diagnostic on stderr.  Never stdout."""
    print(f"sentinel: {msg}", file=sys.stderr)


# --------------------------------------------------------------------------- #
# Option specifications (CONTRACT SS5)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Spec:
    key: str
    domain: str
    check: Callable[[str, "Options"], bool]
    default: Callable[["Options"], str]
    choices: Callable[[], List[str]]

    @property
    def option(self) -> str:
        return OPT_PREFIX + self.key


def _int_domain(lo: int, hi: int) -> Callable[[str, "Options"], bool]:
    def check(value: str, _opts: "Options") -> bool:
        try:
            n = int(value, 10)
        except ValueError:
            return False
        return lo <= n <= hi

    return check


def _enum_domain(values: Sequence[str]) -> Callable[[str, "Options"], bool]:
    allowed = tuple(values)

    def check(value: str, _opts: "Options") -> bool:
        return value in allowed

    return check


def _check_theme(value: str, _opts: "Options") -> bool:
    from . import themes

    return value in themes.list_themes()


def _check_segments(value: str, _opts: "Options") -> bool:
    if value == "":
        return True
    if " " in value or "\t" in value:
        return False
    parts = value.split(",")
    return all(p in SEGMENTS for p in parts) and len(set(parts)) == len(parts)


def _check_always(value: str, opts: "Options") -> bool:
    """Same list grammar as ``segments``, but the empty list is out of domain.

    ``tmux show-option -gqv`` reports an *unset* option as the empty string, so
    an empty ``@sentinel_always`` cannot mean "no segment has a resting state":
    ``generate.sh`` restores the default instead.  Rejecting it here keeps the
    knob from lying about what it did.
    """
    return value != "" and _check_segments(value, opts)


def _check_clock_format(value: str, _opts: "Options") -> bool:
    return bool(CLOCK_FORMAT_RE.match(value))


def _check_accent(value: str, _opts: "Options") -> bool:
    if not 1 <= len(value) <= 4:
        return False
    return not (set(value) & ACCENT_FORBIDDEN)


def _default_accent(opts: "Options") -> str:
    from . import themes

    try:
        return themes.load_glyphs(opts.get("glyphs"))["accent"]
    except Exception:
        return "|"


def _const(value: str) -> Callable[["Options"], str]:
    return lambda _opts: value


def _no_choices() -> List[str]:
    return []


def _theme_choices() -> List[str]:
    from . import themes

    return themes.list_themes()


SPECS: Tuple[Spec, ...] = (
    Spec("theme", "a filename stem in themes/", _check_theme,
         _const("catppuccin-mocha"), _theme_choices),
    Spec("position", "top|bottom", _enum_domain(POSITIONS),
         _const("top"), lambda: list(POSITIONS)),
    Spec("interval", "integer 1..3600", _int_domain(1, 3600),
         _const("10"), _no_choices),
    Spec("glyphs", "nerd|unicode|ascii", _enum_domain(GLYPH_MODES),
         _const("nerd"), lambda: list(GLYPH_MODES)),
    Spec("always", "non-empty comma list (no spaces) of: " + ",".join(SEGMENTS),
         _check_always, _const(ALWAYS_DEFAULT), lambda: list(SEGMENTS)),
    Spec("segments", "comma list (no spaces) of: " + ",".join(SEGMENTS),
         _check_segments, _const(",".join(SEGMENTS)), lambda: list(SEGMENTS)),
    Spec("windows", "hidden|minimal|tabs", _enum_domain(WINDOW_MODES),
         _const("hidden"), lambda: list(WINDOW_MODES)),
    Spec("clock_format", r"strftime matching ^[%A-Za-z0-9 :/.,+-]{1,32}$",
         _check_clock_format, _const("%H:%M"), _no_choices),
    Spec("session_max_length", "integer 1..64", _int_domain(1, 64),
         _const("18"), _no_choices),
    Spec("accent", "1..4 chars, none of \" ' \\ $ ; # or newline",
         _check_accent, _default_accent, _no_choices),
    Spec("disk_warn_gb", "integer 0..100000", _int_domain(0, 100000),
         _const("25"), _no_choices),
    Spec("disk_crit_gb", "integer 0..100000", _int_domain(0, 100000),
         _const("15"), _no_choices),
    Spec("cpu_warn_pct", "integer 0..100", _int_domain(0, 100),
         _const("70"), _no_choices),
    Spec("cpu_crit_pct", "integer 0..100", _int_domain(0, 100),
         _const("90"), _no_choices),
    Spec("battery_warn_pct", "integer 0..100", _int_domain(0, 100),
         _const("50"), _no_choices),
    Spec("battery_crit_pct", "integer 0..100", _int_domain(0, 100),
         _const("20"), _no_choices),
)

SPEC_BY_KEY: Dict[str, Spec] = {s.key: s for s in SPECS}
KEYS: Tuple[str, ...] = tuple(s.key for s in SPECS)

# Options that existed in a shipped release and no longer do. Kept so an
# upgrade reports them instead of either bricking the CLI (an unknown-option
# abort) or dropping a user's setting without a word.
REMOVED_OPTIONS: Dict[str, str] = {
    "alerts_only": (
        "use @sentinel_always to choose which segments stay visible "
        f'when quiet (default "{ALWAYS_DEFAULT}"; the old "off" is every '
        "segment named)"
    ),
}


# --------------------------------------------------------------------------- #
# tmux plumbing
# --------------------------------------------------------------------------- #


def tmux_argv(args: Sequence[str]) -> Tuple[str, ...]:
    """Build a tmux command line, honouring the TMUX_SENTINEL_SOCKET override.

    The override exists so tests (and anyone debugging) can drive a private
    server instead of the user's own.  ``scripts/generate.sh`` reads the same
    variable, so the CLI and the generator always agree on which server holds
    the options.
    """
    socket = os.environ.get("TMUX_SENTINEL_SOCKET")
    if socket:
        return ("tmux", "-L", socket, "-f", "/dev/null", *args)
    return ("tmux", *args)


def tmux(*args: str) -> Tuple[int, str, str]:
    """Run tmux; return (rc, stdout, stderr).  Never raises for tmux failure."""
    try:
        proc = subprocess.run(
            tmux_argv(args), capture_output=True, text=True, timeout=10
        )
    except FileNotFoundError:
        return 127, "", "tmux not found in PATH"
    except subprocess.TimeoutExpired:
        return 124, "", "tmux timed out"
    return proc.returncode, proc.stdout, proc.stderr


_NO_SERVER_RE = re.compile(r"no server running|error connecting to", re.I)


def server_running() -> bool:
    """True when a tmux server answers on the current socket."""
    rc, _out, err = tmux("show-options", "-g")
    if rc == 0:
        return True
    if _NO_SERVER_RE.search(err):
        return False
    # tmux exists but failed for another reason: treat as unavailable.
    return False


def _server_options() -> Optional[Dict[str, str]]:
    """Snapshot every global option in one fork, or None when no server."""
    rc, out, err = tmux("show-options", "-g")
    if rc != 0:
        if not _NO_SERVER_RE.search(err) and err.strip():
            warn(f"tmux show-options failed: {err.strip()}")
        return None
    result: Dict[str, str] = {}
    for line in out.splitlines():
        if not line.startswith(OPT_PREFIX):
            continue
        name, _, raw = line.partition(" ")
        result[name] = _unquote_tmux(raw)
    return result


def _unquote_tmux(raw: str) -> str:
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        return raw[1:-1]
    return raw


# --------------------------------------------------------------------------- #
# options.conf persistence
# --------------------------------------------------------------------------- #


def read_options_conf() -> Dict[str, str]:
    """Parse options.conf.  Malformed content raises; it is never ignored."""
    if not OPTIONS_CONF.exists():
        return {}
    try:
        text = OPTIONS_CONF.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(f"cannot read {OPTIONS_CONF}: {exc}") from exc
    except UnicodeDecodeError as exc:
        raise ConfigError(f"{OPTIONS_CONF} is not valid UTF-8: {exc}") from exc

    out: Dict[str, str] = {}
    for lineno, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        m = _OPTIONS_LINE_RE.match(line)
        if not m:
            raise ConfigError(
                f"{OPTIONS_CONF}:{lineno}: malformed line: {line!r}\n"
                '  expected: set -ogq @sentinel_<key> "<value>"\n'
                "  refusing to guess; fix or delete the file."
            )
        key, value = m.group(1), m.group(2)
        if key in REMOVED_OPTIONS:
            # A removed option must not brick the CLI for anyone upgrading.
            # Say so once and carry on; an option we never had still aborts,
            # because that is a typo we must not silently discard.
            warn(f"{OPTIONS_CONF}:{lineno}: @sentinel_{key} was removed in "
                 f"{VERSION} and is ignored — {REMOVED_OPTIONS[key]}")
            continue
        if key not in SPEC_BY_KEY:
            raise ConfigError(
                f"{OPTIONS_CONF}:{lineno}: unknown option @sentinel_{key}"
            )
        out[key] = value
    return out


def _atomic_write(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically: temp file in the same dir, fsync, replace."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def write_options_conf(values: Dict[str, str]) -> None:
    lines = [
        "# tmux-sentinel persisted options. Managed by `sentinel set` / the TUI.",
        "# Sourced by sentinel.tmux with -ogq, so ~/.tmux.conf always wins.",
    ]
    for key in KEYS:
        if key in values:
            lines.append(f'set -ogq {OPT_PREFIX}{key} "{values[key]}"')
    _atomic_write(OPTIONS_CONF, "\n".join(lines) + "\n")


# --------------------------------------------------------------------------- #
# config.json -> options.conf migration (CONTRACT SS6)
# --------------------------------------------------------------------------- #


def migrate_legacy_json() -> Optional[str]:
    """One-shot conversion of a v1 config.json.  Returns a notice, or None."""
    if OPTIONS_CONF.exists() or not LEGACY_JSON.exists():
        return None
    try:
        data = json.loads(LEGACY_JSON.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ConfigError(
            f"cannot migrate {LEGACY_JSON}: {exc}\n"
            "  fix or remove the file, then re-run."
        ) from exc
    if not isinstance(data, dict):
        raise ConfigError(f"{LEGACY_JSON}: expected a JSON object")

    flat: Dict[str, str] = {}

    def put(key: str, value: object) -> None:
        if value is None:
            return
        text = str(value)
        spec = SPEC_BY_KEY.get(key)
        if spec is None:
            return
        probe = Options({}, {})
        if spec.check(text, probe):
            flat[key] = text

    put("theme", data.get("theme"))
    put("position", data.get("position"))
    put("interval", data.get("interval"))
    put("glyphs", data.get("glyph_mode"))
    segs = data.get("segments")
    if isinstance(segs, dict):
        enabled = [s for s in SEGMENTS if segs.get(s, True)]
        put("segments", ",".join(enabled))
    windows = data.get("windows")
    if isinstance(windows, dict):
        put("windows", windows.get("mode"))
    put("clock_format", data.get("clock_format"))
    left = data.get("left")
    if isinstance(left, dict):
        put("session_max_length", left.get("max_session_length"))
        put("accent", left.get("accent_symbol"))
    thresholds = data.get("thresholds")
    if isinstance(thresholds, dict):
        for key in (
            "disk_warn_gb",
            "disk_crit_gb",
            "cpu_warn_pct",
            "cpu_crit_pct",
            "battery_warn_pct",
            "battery_crit_pct",
        ):
            put(key, thresholds.get(key))

    write_options_conf(flat)
    migrated = LEGACY_JSON.with_suffix(".json.migrated")
    os.replace(LEGACY_JSON, migrated)
    return (
        f"migrated {len(flat)} setting(s) from config.json to {OPTIONS_CONF}; "
        f"old file kept as {migrated.name}"
    )


# --------------------------------------------------------------------------- #
# Options
# --------------------------------------------------------------------------- #


class Options:
    """Validated view of the ``@sentinel_*`` namespace.

    Resolution order per key: live tmux server -> persisted options.conf ->
    documented default.  Any value failing its domain check is replaced by the
    default and reported on stderr.
    """

    def __init__(self, server: Dict[str, str], persisted: Dict[str, str],
                 quiet: bool = False):
        self._server = server
        self._persisted = dict(persisted)
        self._resolved: Dict[str, str] = {}
        self._origin: Dict[str, str] = {}
        self._quiet = quiet
        self._dirty: set = set()
        for spec in SPECS:
            self._resolve(spec)

    # -- resolution -------------------------------------------------------- #

    def _resolve(self, spec: Spec) -> None:
        for source, store in (("tmux", self._server), ("options.conf", self._persisted)):
            if spec.key not in store:
                continue
            value = store[spec.key]
            if value == "":
                continue
            if spec.check(value, self):
                self._resolved[spec.key] = value
                self._origin[spec.key] = source
                return
            if not self._quiet:
                warn(
                    f"{spec.option}={value!r} from {source} is invalid "
                    f"({spec.domain}); using default"
                )
        self._resolved[spec.key] = spec.default(self)
        self._origin[spec.key] = "default"

    # -- access ------------------------------------------------------------ #

    def get(self, key: str) -> str:
        try:
            return self._resolved[key]
        except KeyError:
            raise OptionError(
                f"unknown setting {key!r}; known: {', '.join(KEYS)}"
            ) from None

    def origin(self, key: str) -> str:
        return self._origin.get(key, "default")

    def int_of(self, key: str) -> int:
        return int(self.get(key), 10)

    def _name_list(self, key: str) -> List[str]:
        raw = self.get(key)
        return [] if raw == "" else raw.split(",")

    def segment_list(self) -> List[str]:
        return self._name_list("segments")

    def segment_enabled(self, name: str) -> bool:
        return name in self.segment_list()

    def always_list(self) -> List[str]:
        """Segments given a visible resting state (CONTRACT SS5)."""
        return self._name_list("always")

    def always_enabled(self, name: str) -> bool:
        return name in self.always_list()

    def as_dict(self) -> Dict[str, str]:
        return dict(self._resolved)

    @property
    def dirty(self) -> bool:
        return bool(self._dirty)

    @property
    def dirty_keys(self) -> List[str]:
        return sorted(self._dirty)

    # -- mutation ---------------------------------------------------------- #

    def stage(self, key: str, value: str) -> None:
        """Validate and record a change in memory without touching tmux or disk."""
        spec = SPEC_BY_KEY.get(key)
        if spec is None:
            raise OptionError(
                f"unknown setting {key!r}; known: {', '.join(KEYS)}"
            )
        if not spec.check(value, self):
            raise OptionError(
                f"invalid value {value!r} for {key}: expected {spec.domain}"
            )
        if self._resolved.get(key) == value and key not in self._dirty:
            return
        self._resolved[key] = value
        self._origin[key] = "staged"
        self._dirty.add(key)
        if key == "glyphs" and "accent" not in self._dirty:
            # The accent default follows the glyph set.
            spec_accent = SPEC_BY_KEY["accent"]
            if self._origin.get("accent") == "default":
                self._resolved["accent"] = spec_accent.default(self)

    def _toggle_in_list(self, key: str, name: str) -> bool:
        """Flip ``name``'s membership of the comma list ``key``; return the new state."""
        if name not in SEGMENTS:
            raise OptionError(
                f"unknown segment {name!r}; known: {', '.join(SEGMENTS)}"
            )
        current = set(self._name_list(key))
        if name in current:
            current.discard(name)
            enabled = False
        else:
            current.add(name)
            enabled = True
        self.stage(key, ",".join(s for s in SEGMENTS if s in current))
        return enabled

    def toggle_segment(self, name: str) -> bool:
        return self._toggle_in_list("segments", name)

    def toggle_always(self, name: str) -> bool:
        if self.always_list() == [name]:
            raise OptionError(
                f"{name} is the last segment with a resting state; "
                "@sentinel_always cannot be emptied (tmux reads an empty "
                "option as unset, so the default would come back)"
            )
        return self._toggle_in_list("always", name)

    def persisted_snapshot(self) -> Dict[str, str]:
        """options.conf content after applying staged edits."""
        merged = dict(self._persisted)
        for key in self._dirty:
            merged[key] = self._resolved[key]
        return merged

    def commit(self) -> "ApplyResult":
        """Push staged edits to tmux + options.conf, regenerate, reload."""
        if not self._dirty:
            return apply_all(self)
        merged = self.persisted_snapshot()
        write_options_conf(merged)
        self._persisted = merged
        errors: List[str] = []
        if server_running():
            for key in sorted(self._dirty):
                rc, _out, err = tmux(
                    "set-option", "-g", OPT_PREFIX + key, self._resolved[key]
                )
                if rc != 0:
                    errors.append(
                        f"tmux set-option {OPT_PREFIX}{key}: "
                        f"{err.strip() or f'exit {rc}'}"
                    )
        for key in list(self._dirty):
            self._origin[key] = "options.conf"
        self._dirty.clear()
        result = apply_all(self)
        result.errors[0:0] = errors
        return result


def load(quiet: bool = False, migrate: bool = True) -> Options:
    """Load the validated option set.  Raises ConfigError on a broken options.conf."""
    if migrate:
        notice = migrate_legacy_json()
        if notice and not quiet:
            warn(notice)
    persisted = read_options_conf()
    server = _server_options()
    server_bare = {}
    if server is not None:
        for name, value in server.items():
            server_bare[name[len(OPT_PREFIX):]] = value
    return Options(server_bare, persisted, quiet=quiet)


# --------------------------------------------------------------------------- #
# generate.sh + reload
# --------------------------------------------------------------------------- #


@dataclass
class ApplyResult:
    generated: bool
    reloaded: bool
    errors: List[str]
    notes: List[str]

    @property
    def ok(self) -> bool:
        return not self.errors


def run_generate() -> Tuple[bool, str]:
    """Invoke scripts/generate.sh.  Returns (ok, diagnostic text)."""
    if not GENERATE_SH.exists():
        return False, f"{GENERATE_SH} is missing; run `make` in {REPO_ROOT}"
    try:
        proc = subprocess.run(
            [str(GENERATE_SH)],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=str(REPO_ROOT),
        )
    except PermissionError:
        return False, f"{GENERATE_SH} is not executable (chmod +x it)"
    except subprocess.TimeoutExpired:
        return False, f"{GENERATE_SH} timed out after 30s"
    detail = (proc.stderr.strip() or proc.stdout.strip())
    if proc.returncode != 0:
        return False, f"generate.sh exited {proc.returncode}: {detail or 'no output'}"
    return True, detail


def reload_tmux() -> Tuple[bool, Optional[str], bool]:
    """Source the generated conf and refresh clients.

    Returns (ok, error_text, server_running).  A missing server is not an error;
    it is reported through the third element so callers can say so plainly.
    """
    if not server_running():
        return True, None, False
    if not SENTINEL_CONF.exists():
        return False, f"{SENTINEL_CONF} does not exist; nothing to source", True
    rc, _out, err = tmux("source-file", str(SENTINEL_CONF))
    if rc != 0:
        return False, err.strip() or f"tmux source-file exited {rc}", True
    rc, _out, err = tmux("refresh-client", "-S")
    if rc != 0 and "no current client" not in err.lower():
        # A detached server legitimately has no client to refresh; the sourced
        # config still took effect, so that case is not a failure.
        return False, err.strip() or f"tmux refresh-client exited {rc}", True
    return True, None, True


def apply_all(_opts: Optional[Options] = None) -> ApplyResult:
    """Regenerate artifacts from the live options and reload tmux."""
    errors: List[str] = []
    notes: List[str] = []
    ok, detail = run_generate()
    if not ok:
        errors.append(detail)
        return ApplyResult(False, False, errors, notes)
    if detail:
        notes.append(detail)
    reloaded, err, running = reload_tmux()
    if not reloaded and err:
        errors.append(f"tmux reload failed: {err}")
    if not running:
        notes.append("no tmux server running; artifacts written, nothing to reload")
    return ApplyResult(True, reloaded and running, errors, notes)


# --------------------------------------------------------------------------- #
# sentinel.state serialization (preview only)
# --------------------------------------------------------------------------- #

_STATE_THRESHOLDS = (
    "disk_warn_gb",
    "disk_crit_gb",
    "cpu_warn_pct",
    "cpu_crit_pct",
    "battery_warn_pct",
    "battery_crit_pct",
)

_GLYPH_STATE_KEYS = (
    ("glyph_sep", "sep"),
    ("glyph_thermal", "thermal"),
    ("glyph_sleep", "sleep"),
    ("glyph_disk", "disk"),
    ("glyph_battery_full", "battery_full"),
    ("glyph_battery_mid", "battery_mid"),
    ("glyph_battery_low", "battery_low"),
    ("glyph_cpu", "cpu"),
    ("glyph_memory", "memory"),
    ("glyph_clients", "clients"),
)

_COLOR_STATE_KEYS = (
    ("color_fg", "fg"),
    ("color_dim", "dim"),
    ("color_val", "val"),
    ("color_sep", "sep"),
    ("color_alert", "alert"),
    ("color_warn", "warn"),
    ("color_peach", "peach"),
    ("color_info", "info"),
)


def state_text(opts: Options) -> str:
    """Serialize ``opts`` into CONTRACT SS1 state grammar.

    Used only to feed ``sentinel-status --simulate`` a state reflecting
    *unsaved* edits during preview/TUI.  The authoritative on-disk state is
    written by ``scripts/generate.sh``; ``sentinel doctor`` cross-checks this
    serializer against it so the two cannot silently drift.
    """
    from . import themes

    palette = themes.load_palette(opts.get("theme"))
    glyphs = themes.load_glyphs(opts.get("glyphs"))

    lines = ["version=1"]
    resting = set(opts.always_list())
    for seg in SEGMENTS:
        lines.append(f"always_{seg}={1 if seg in resting else 0}")
    lines.append(f"clock_format={opts.get('clock_format')}")
    for state_key, pal_key in _COLOR_STATE_KEYS:
        lines.append(f"{state_key}={palette[pal_key]}")
    enabled = set(opts.segment_list())
    for seg in SEGMENTS:
        lines.append(f"seg_{seg}={1 if seg in enabled else 0}")
    for state_key, glyph_key in _GLYPH_STATE_KEYS:
        lines.append(f"{state_key}={glyphs[glyph_key]}")
    for key in _STATE_THRESHOLDS:
        lines.append(f"{key}={opts.get(key)}")
    return "\n".join(lines) + "\n"


def parse_state(text: str) -> Dict[str, str]:
    """Parse CONTRACT SS1 grammar.  Trailing whitespace in values is preserved."""
    out: Dict[str, str] = {}
    for raw in text.split("\n"):
        line = raw.rstrip("\r")
        if not line or line.startswith("#"):
            continue
        key, sep, value = line.partition("=")
        if not sep or not re.fullmatch(r"[a-z0-9_]+", key):
            continue
        out[key] = value
    return out


def write_temp_state(opts: Options) -> Path:
    """Write a throwaway state file for preview rendering."""
    fd, path = tempfile.mkstemp(prefix="sentinel-preview.", suffix=".state")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(state_text(opts))
    return Path(path)


# --------------------------------------------------------------------------- #
# Engine invocation
# --------------------------------------------------------------------------- #


class EngineMissing(Exception):
    """bin/sentinel-status is absent or not executable."""


def status_binary() -> Path:
    """Locate the engine, honouring SENTINEL_STATUS_BIN for tests."""
    override = os.environ.get("SENTINEL_STATUS_BIN")
    candidate = Path(override) if override else STATUS_BIN
    if not candidate.exists():
        raise EngineMissing(
            f"{candidate} not found.\n"
            f"  Build it first:  make -C {REPO_ROOT}\n"
            "  The preview and TUI render the real bar; there is no Python fallback."
        )
    if not os.access(candidate, os.X_OK):
        raise EngineMissing(f"{candidate} is not executable; run: chmod +x {candidate}")
    return candidate


def run_engine(args: Iterable[str], timeout: float = 5.0) -> subprocess.CompletedProcess:
    binary = status_binary()
    return subprocess.run(
        [str(binary), *args], capture_output=True, text=True, timeout=timeout
    )
