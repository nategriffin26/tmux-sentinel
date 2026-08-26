"""Command line front end for tmux-sentinel.

Python generates nothing and stores no configuration of its own: every setting is
a tmux option (CONTRACT SS5), every artifact is produced by ``scripts/generate.sh``
and the bar itself is rendered only by ``bin/sentinel-status``.

Data goes to stdout.  Every diagnostic, warning and confirmation goes to stderr.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

from . import options as O
from . import renderer
from . import themes

VERSION = O.VERSION

BEGIN_MARK = "# >>> tmux-sentinel >>>"
END_MARK = "# <<< tmux-sentinel <<<"

OK = "ok"
WARN = "warn"
FAIL = "FAIL"


def err(msg: str) -> None:
    print(msg, file=sys.stderr)


def note(msg: str) -> None:
    print(f"sentinel: {msg}", file=sys.stderr)


def out(msg: str = "") -> None:
    print(msg)


# --------------------------------------------------------------------------- #
# shared helpers
# --------------------------------------------------------------------------- #


def _report_apply(result: O.ApplyResult) -> int:
    for line in result.notes:
        note(line)
    for line in result.errors:
        err(f"sentinel: error: {line}")
    return 0 if result.ok else 1


def _commit(opts: O.Options, summary: str) -> int:
    result = opts.commit()
    rc = _report_apply(result)
    if rc == 0:
        note(summary)
    else:
        err("sentinel: settings were persisted but applying them failed")
    return rc


def _tmux_conf_path() -> Path:
    """The tmux config to edit: an existing one if there is one, else ~/.tmux.conf."""
    home_conf = Path.home() / ".tmux.conf"
    xdg = os.environ.get("XDG_CONFIG_HOME")
    candidates = [home_conf]
    if xdg:
        candidates.append(Path(xdg) / "tmux" / "tmux.conf")
    else:
        candidates.append(Path.home() / ".config" / "tmux" / "tmux.conf")
    for path in candidates:
        if path.exists():
            return path
    return home_conf


# --------------------------------------------------------------------------- #
# customize / tui
# --------------------------------------------------------------------------- #


def cmd_tui(args: argparse.Namespace) -> int:
    from .tui import start_tui

    return start_tui()


# --------------------------------------------------------------------------- #
# preview
# --------------------------------------------------------------------------- #


def cmd_preview(args: argparse.Namespace) -> int:
    opts = O.load()
    if getattr(args, "theme", None):
        opts.stage("theme", args.theme)
    if getattr(args, "glyphs", None):
        opts.stage("glyphs", args.glyphs)

    width = args.width or shutil.get_terminal_size((80, 24)).columns
    session = args.session or "main"
    sims = (args.sim,) if args.sim != "both" else renderer.SIM_STATES

    palette = themes.load_palette(opts.get("theme"))
    glyphs = themes.load_glyphs(opts.get("glyphs"))

    header = (f"{palette['name']} | glyphs: {glyphs['name']} | "
              f"position: {opts.get('position')} | "
              f"always: {opts.get('always') or '(none)'}")
    out(renderer.truncate_to_width(header, width))
    out()
    for sim in sims:
        bar = renderer.render_preview_bar(
            opts, width=width, sim=sim, session_name=session
        )
        measured = renderer.preview_width(bar)
        out(renderer.truncate_to_width(f"{sim} ({measured} of {width} cols):",
                                       width))
        out(bar)
        out()
    return 0


# --------------------------------------------------------------------------- #
# theme
# --------------------------------------------------------------------------- #


def cmd_theme(args: argparse.Namespace) -> int:
    opts = O.load()
    if not args.name:
        current = opts.get("theme")
        for stem in themes.list_themes():
            palette = themes.load_palette(stem)
            r, g, b = renderer.hex_to_rgb(palette["accent"])
            swatch = f"\033[38;2;{r};{g};{b}m\u2588\u2588\033[0m"
            marker = "*" if stem == current else " "
            out(f"{marker} {swatch} {stem:<22} {palette['name']} "
                f"- {palette['description']}")
        note("run `sentinel theme <name>` to apply one")
        return 0

    opts.stage("theme", args.name)
    return _commit(opts, f"theme set to {args.name}")


# --------------------------------------------------------------------------- #
# toggle / set / get
# --------------------------------------------------------------------------- #


def cmd_toggle(args: argparse.Namespace) -> int:
    opts = O.load()
    enabled = opts.toggle_segment(args.segment)
    state = "enabled" if enabled else "disabled"
    return _commit(opts, f"segment {args.segment} {state}")


def cmd_always(args: argparse.Namespace) -> int:
    opts = O.load()
    on = opts.toggle_always(args.segment)
    if args.segment in O.INERT_ALWAYS:
        note(f"{args.segment} has no quiet state; it renders either way")
    current = opts.get("always") or "(none)"
    state = "always visible" if on else "only when it has something to report"
    return _commit(opts, f"{args.segment} {state}; always = {current}")


def cmd_set(args: argparse.Namespace) -> int:
    opts = O.load()
    opts.stage(args.key, args.value)
    return _commit(opts, f"{args.key} = {args.value}")


def cmd_get(args: argparse.Namespace) -> int:
    opts = O.load()
    if args.key:
        value = opts.get(args.key)
        out(value)
        if args.verbose:
            note(f"{args.key} resolved from {opts.origin(args.key)}")
        return 0
    for key in O.KEYS:
        if args.verbose:
            out(f"{key}={opts.get(key)}\t# {opts.origin(key)}")
        else:
            out(f"{key}={opts.get(key)}")
    return 0


# --------------------------------------------------------------------------- #
# apply
# --------------------------------------------------------------------------- #


def cmd_apply(args: argparse.Namespace) -> int:
    O.load()  # surfaces invalid options and migrates legacy config first
    return _report_apply(O.apply_all())


# --------------------------------------------------------------------------- #
# install / uninstall
# --------------------------------------------------------------------------- #


def _backup(path: Path) -> Path:
    stamp = time.strftime("%Y%m%d%H%M%S")
    backup = path.with_name(path.name + f".sentinel-backup-{stamp}")
    shutil.copy2(path, backup)
    return backup


def _install_block() -> str:
    return (
        f"{BEGIN_MARK}\n"
        f"source-file {O.SENTINEL_CONF}\n"
        f"{END_MARK}\n"
    )


def cmd_install(args: argparse.Namespace) -> int:
    conf = _tmux_conf_path()
    block = _install_block()
    content = conf.read_text(encoding="utf-8") if conf.exists() else ""
    already = BEGIN_MARK in content

    if already:
        out(f"{conf} already contains this block:")
    elif args.dry_run:
        out(f"would add this block to {conf}:")
    else:
        out(f"adding this block to {conf}:")
    for line in block.rstrip("\n").split("\n"):
        out("  " + line)

    if args.dry_run:
        note("dry run: no files were written, nothing was reloaded")
        return 0

    if not already:
        if conf.exists():
            backup = _backup(conf)
            note(f"backed up {conf} to {backup}")
            sep = "" if content.endswith("\n") or content == "" else "\n"
            new = content + sep + "\n" + block
        else:
            conf.parent.mkdir(parents=True, exist_ok=True)
            new = block
        O._atomic_write(conf, new)
        note(f"wrote {conf}")

    return _report_apply(O.apply_all())


def _strip_block(content: str) -> Tuple[str, bool]:
    lines = content.split("\n")
    kept: List[str] = []
    inside = False
    removed = False
    conf_line = f"source-file {O.SENTINEL_CONF}"
    for line in lines:
        if line.strip() == BEGIN_MARK:
            inside = True
            removed = True
            continue
        if inside:
            if line.strip() == END_MARK:
                inside = False
            continue
        if line.strip() == conf_line:
            removed = True
            continue
        kept.append(line)
    return "\n".join(kept), removed


def cmd_uninstall(args: argparse.Namespace) -> int:
    conf = _tmux_conf_path()
    removed_any = False

    if conf.exists():
        content = conf.read_text(encoding="utf-8")
        new, removed = _strip_block(content)
        if removed:
            if args.dry_run:
                note(f"would remove the tmux-sentinel block from {conf}")
            else:
                backup = _backup(conf)
                note(f"backed up {conf} to {backup}")
                O._atomic_write(conf, new)
                note(f"removed the tmux-sentinel block from {conf}")
            removed_any = True
        else:
            note(f"{conf} contains no tmux-sentinel block")
    else:
        note(f"{conf} does not exist")

    generated = [O.SENTINEL_CONF, O.STATE_FILE, O.CONFIG_DIR / "env.sh"]
    for path in generated:
        if not path.exists():
            continue
        if args.dry_run:
            note(f"would remove {path}")
        else:
            path.unlink()
            note(f"removed {path}")
        removed_any = True

    keep = _decide_keep_options(args)
    if O.OPTIONS_CONF.exists():
        if keep:
            note(f"kept your settings in {O.OPTIONS_CONF} "
                 "(pass --purge to delete them)")
        elif args.dry_run:
            note(f"would remove {O.OPTIONS_CONF}")
        else:
            O.OPTIONS_CONF.unlink()
            note(f"removed {O.OPTIONS_CONF}")

    if args.dry_run:
        note("dry run: nothing was written or reloaded")
        return 0

    if not removed_any:
        note("nothing to uninstall")
        return 0

    if not O.server_running():
        note("no tmux server running; nothing to reload")
    elif conf.exists():
        rc, _stdout, stderr = O.tmux("source-file", str(conf))
        if rc != 0:
            err(f"sentinel: error: tmux source-file {conf}: "
                f"{stderr.strip() or f'exit {rc}'}")
            return 1
        O.tmux("refresh-client", "-S")
        note("reloaded tmux")
    return 0


def _decide_keep_options(args: argparse.Namespace) -> bool:
    if args.purge:
        return False
    if args.keep_options:
        return True
    if not O.OPTIONS_CONF.exists():
        return True
    if not sys.stdin.isatty():
        return True
    answer = input(f"Delete your saved settings at {O.OPTIONS_CONF}? [y/N] ")
    return answer.strip().lower() not in ("y", "yes")


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #


class Report:
    def __init__(self) -> None:
        self.rows: List[Tuple[str, str, str]] = []

    def add(self, status: str, name: str, detail: str = "") -> None:
        self.rows.append((status, name, detail))

    @property
    def failed(self) -> bool:
        return any(status == FAIL for status, _n, _d in self.rows)

    def emit(self) -> None:
        for status, name, detail in self.rows:
            mark = {OK: "  ok  ", WARN: " warn ", FAIL: " FAIL "}[status]
            out(f"[{mark}] {name}" + (f": {detail}" if detail else ""))


def _status_right_command(conf_text: str) -> Optional[str]:
    """Extract the shell command from the generated ``status-right`` ``#()``."""
    for line in conf_text.split("\n"):
        if "status-right" not in line:
            continue
        start = line.find("#(")
        if start < 0:
            continue
        depth = 0
        for idx in range(start + 1, len(line)):
            if line[idx] == "(":
                depth += 1
            elif line[idx] == ")":
                depth -= 1
                if depth == 0:
                    return line[start + 2:idx]
    return None


_FORMAT_RE = re.compile(r"#\{[^}]*\}")


def _expand_tmux_formats(command: str) -> str:
    """Resolve ``#{...}`` placeholders so the command can run under a plain sh.

    tmux expands these before invoking the shell; ``sh`` would treat the ``#``
    as a comment and silently swallow the rest of the line, which is exactly the
    kind of "looks fine, produces nothing" failure doctor exists to catch.
    """
    def substitute(match: "re.Match[str]") -> str:
        placeholder = match.group(0)
        rc, stdout, _err = O.tmux("display-message", "-p", placeholder)
        value = stdout.strip() if rc == 0 else ""
        return value or "1"

    return _FORMAT_RE.sub(substitute, command)


def _tput_colors() -> Optional[int]:
    exe = shutil.which("tput")
    if not exe:
        return None
    proc = subprocess.run([exe, "colors"], capture_output=True, text=True)
    try:
        return int(proc.stdout.strip(), 10)
    except ValueError:
        return None


def cmd_doctor(args: argparse.Namespace) -> int:
    if args.fix:
        note("--fix: regenerating artifacts")
        ok, detail = O.run_generate()
        if not ok:
            err(f"sentinel: error: {detail}")
        elif detail:
            note(detail)
        conf = _tmux_conf_path()
        content = conf.read_text(encoding="utf-8") if conf.exists() else ""
        if BEGIN_MARK not in content:
            note(f"--fix: linking sentinel into {conf}")
            fix_args = argparse.Namespace(dry_run=False)
            cmd_install(fix_args)
        reloaded, rerr, running = O.reload_tmux()
        if not reloaded and rerr:
            err(f"sentinel: error: tmux reload failed: {rerr}")
        elif not running:
            note("--fix: no tmux server running; skipped reload")

    report = Report()

    # -- tmux ----------------------------------------------------------------
    tmux_path = shutil.which("tmux")
    if tmux_path:
        rc, stdout, _e = O.tmux("-V")
        report.add(OK if rc == 0 else FAIL, "tmux",
                   f"{stdout.strip() or 'unknown version'} ({tmux_path})")
    else:
        report.add(FAIL, "tmux", "not found in PATH")

    running = O.server_running()
    report.add(OK if running else WARN, "tmux server",
               "responding" if running else "no server running on this socket")

    uname = subprocess.run(["uname", "-sm"], capture_output=True, text=True)
    report.add(OK, "platform", uname.stdout.strip())

    # -- options -------------------------------------------------------------
    try:
        opts = O.load(quiet=False)
        persisted = O.read_options_conf()
        report.add(OK, "options.conf",
                   f"{O.OPTIONS_CONF} ({len(persisted)} setting(s) persisted)"
                   if O.OPTIONS_CONF.exists()
                   else f"{O.OPTIONS_CONF} absent; using defaults")
    except O.ConfigError as exc:
        report.add(FAIL, "options.conf", str(exc).replace("\n", " "))
        report.emit()
        return 1

    # -- engine binary -------------------------------------------------------
    engine_ok = False
    try:
        binary = O.status_binary()
        proc = O.run_engine(["--version"])
        if proc.returncode == 0:
            report.add(OK, "engine", f"{proc.stdout.strip()} ({binary})")
            engine_ok = True
        else:
            report.add(FAIL, "engine",
                       f"{binary} --version exited {proc.returncode}: "
                       f"{proc.stderr.strip()}")
    except O.EngineMissing as exc:
        report.add(FAIL, "engine", str(exc).replace("\n", " ").strip())

    # -- selftest ------------------------------------------------------------
    if engine_ok:
        proc = O.run_engine(["--selftest"], timeout=10)
        lines = [ln for ln in proc.stdout.split("\n") if ln.strip()]
        report.add(OK if proc.returncode == 0 else FAIL, "engine selftest",
                   f"{len(lines)} segment(s) probed, exit {proc.returncode}")
        for line in lines:
            name, _sep, rest = line.partition(":")
            failed = rest.strip().lower().endswith(("failed", "error", "fail"))
            report.add(FAIL if failed else OK, f"  segment {name.strip()}",
                       rest.strip())
        if proc.stderr.strip():
            report.add(WARN, "  selftest stderr", proc.stderr.strip())

    # -- generated conf ------------------------------------------------------
    if not O.SENTINEL_CONF.exists():
        report.add(FAIL, "sentinel.conf",
                   f"{O.SENTINEL_CONF} missing; run `sentinel apply` "
                   "(or `sentinel doctor --fix`)")
    else:
        conf_text = O.SENTINEL_CONF.read_text(encoding="utf-8")
        report.add(OK, "sentinel.conf", str(O.SENTINEL_CONF))
        command = _status_right_command(conf_text)
        if command is None:
            report.add(FAIL, "status-right",
                       "no #(...) command found in the generated conf")
        else:
            reduced = "status-fallback" in command
            report.add(WARN if reduced else OK, "renderer",
                       "reduced shell fallback active (disk/cpu/memory/clock only); "
                       "run `make` to build the native engine"
                       if reduced else "native engine")
            runnable = _expand_tmux_formats(command)
            proc = subprocess.run(
                ["sh", "-c", runnable], capture_output=True, text=True, timeout=15
            )
            if proc.returncode != 0:
                report.add(FAIL, "status-right exec",
                           f"exit {proc.returncode}: "
                           f"{proc.stderr.strip() or 'no stderr'}")
            elif not proc.stdout.strip():
                report.add(FAIL, "status-right exec",
                           "command produced no output; the bar would be empty")
            else:
                text = renderer.plain_text(proc.stdout)
                report.add(OK, "status-right exec",
                           f"{len(proc.stdout)} bytes, renders {text.strip()!r}")

        if running:
            rc, _o, stderr = O.tmux("source-file", str(O.SENTINEL_CONF))
            if rc != 0:
                report.add(FAIL, "tmux source-file",
                           stderr.strip() or f"exit {rc}")
            elif stderr.strip():
                report.add(WARN, "tmux source-file", stderr.strip())
            else:
                report.add(OK, "tmux source-file", "accepted with no complaints")

    # -- state file ----------------------------------------------------------
    if not O.STATE_FILE.exists():
        report.add(FAIL, "sentinel.state",
                   f"{O.STATE_FILE} missing; run `sentinel apply`")
    else:
        on_disk = O.parse_state(O.STATE_FILE.read_text(encoding="utf-8"))
        if on_disk.get("version") != "1":
            report.add(FAIL, "sentinel.state",
                       f"version={on_disk.get('version')!r}, expected 1")
        else:
            expected = O.parse_state(O.state_text(opts))
            drift = sorted(
                k for k in set(expected) | set(on_disk)
                if expected.get(k) != on_disk.get(k)
            )
            if drift:
                report.add(FAIL, "sentinel.state",
                           "disagrees with the live options for: "
                           + ", ".join(drift)
                           + " (run `sentinel apply`)")
            else:
                report.add(OK, "sentinel.state",
                           f"{len(on_disk)} keys, agrees with the live options")

    # -- ~/.tmux.conf --------------------------------------------------------
    conf = _tmux_conf_path()
    if not conf.exists():
        report.add(FAIL, "tmux config",
                   f"{conf} does not exist; run `sentinel install`")
    else:
        content = conf.read_text(encoding="utf-8")
        if BEGIN_MARK in content or f"source-file {O.SENTINEL_CONF}" in content:
            report.add(OK, "tmux config", f"{conf} sources sentinel.conf")
        elif "@sentinel_" in content:
            report.add(OK, "tmux config",
                       f"{conf} sets @sentinel_* options (TPM install)")
        else:
            report.add(FAIL, "tmux config",
                       f"{conf} neither sources sentinel.conf nor sets "
                       "@sentinel_* options; run `sentinel install`")

    # -- terminal ------------------------------------------------------------
    term = os.environ.get("TERM", "(unset)")
    colors = _tput_colors()
    report.add(OK if colors and colors >= 256 else WARN, "terminal",
               f"TERM={term}, colors={colors if colors is not None else 'unknown'}"
               + ("" if colors and colors >= 256
                  else " (truecolor themes need a 256-colour terminal)"))

    lang = os.environ.get("LC_ALL") or os.environ.get("LC_CTYPE") \
        or os.environ.get("LANG") or ""
    utf8 = "utf-8" in lang.lower() or "utf8" in lang.lower()
    mode = opts.get("glyphs")
    if mode == "ascii":
        report.add(OK, "glyphs", "ascii: safe in any terminal")
    elif not utf8:
        report.add(FAIL, "glyphs",
                   f"{mode} needs a UTF-8 locale but LANG/LC_CTYPE is {lang!r}; "
                   "run `sentinel set glyphs ascii`")
    elif mode == "nerd":
        report.add(WARN, "glyphs",
                   "nerd: requires a Nerd Font-patched font; if you see boxes, "
                   "run `sentinel set glyphs unicode`")
    else:
        report.add(OK, "glyphs", "unicode: UTF-8 locale detected")

    report.emit()
    if report.failed:
        err("sentinel: doctor found problems; see the FAIL lines above")
        return 1
    return 0


# --------------------------------------------------------------------------- #
# completion
# --------------------------------------------------------------------------- #


def cmd_completion(args: argparse.Namespace) -> int:
    from .completions import render_completion, write_completions

    if args.write:
        written = write_completions()
        for path in written:
            note(f"wrote {path}")
        return 0
    out(render_completion(args.shell))
    return 0


# --------------------------------------------------------------------------- #
# parser
# --------------------------------------------------------------------------- #


def _bare(args: argparse.Namespace) -> int:
    """No subcommand: the TUI when interactive, a preview otherwise."""
    if sys.stdin.isatty() and sys.stdout.isatty():
        return cmd_tui(args)
    return cmd_preview(args)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="sentinel",
        description="tmux-sentinel: a quiet tmux status bar, "
                    "configured through tmux options.",
    )
    parser.add_argument("--version", action="version",
                        version=f"sentinel {VERSION}")
    # The root parser carries the defaults every handler may read, so a bare
    # invocation cannot fail on a missing attribute.
    parser.set_defaults(
        func=_bare, theme=None, glyphs=None, session=None, width=None,
        sim="both", verbose=False,
    )

    sub = parser.add_subparsers(dest="command", metavar="<command>")

    p = sub.add_parser("customize", aliases=["tui"],
                       help="interactive curses customizer")
    p.set_defaults(func=cmd_tui)

    p = sub.add_parser("preview", help="render the real bar in this terminal")
    p.add_argument("--theme", "-t", help="preview a theme without applying it")
    p.add_argument("--glyphs", "-g", choices=list(O.GLYPH_MODES),
                   help="preview a glyph set without applying it")
    p.add_argument("--session", "-s", default=None,
                   help="simulated session name")
    p.add_argument("--width", "-w", type=int, default=None,
                   help="bar width in columns (default: terminal width)")
    p.add_argument("--sim", choices=["healthy", "alert", "both"],
                   default="both", help="which simulated state to render")
    p.set_defaults(func=cmd_preview)

    p = sub.add_parser("theme", aliases=["themes"], help="list or set the theme")
    p.add_argument("name", nargs="?", help="theme to apply")
    p.set_defaults(func=cmd_theme)

    p = sub.add_parser("toggle", help="enable/disable one segment")
    p.add_argument("segment", choices=list(O.SEGMENTS), metavar="<segment>",
                   help="one of: " + ", ".join(O.SEGMENTS))
    p.set_defaults(func=cmd_toggle)

    p = sub.add_parser("always",
                       help="flip one segment's resting visibility on/off")
    p.add_argument("segment", choices=list(O.SEGMENTS), metavar="<segment>",
                   help="one of: " + ", ".join(O.SEGMENTS))
    p.set_defaults(func=cmd_always)

    p = sub.add_parser("set", help="set one setting")
    p.add_argument("key", metavar="<key>", help="one of: " + ", ".join(O.KEYS))
    p.add_argument("value", metavar="<value>")
    p.set_defaults(func=cmd_set)

    p = sub.add_parser("get", help="print settings")
    p.add_argument("key", nargs="?", metavar="<key>")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="also report where each value came from")
    p.set_defaults(func=cmd_get)

    p = sub.add_parser("apply", aliases=["reload"],
                       help="regenerate artifacts and reload tmux")
    p.set_defaults(func=cmd_apply)

    p = sub.add_parser("doctor", help="diagnose the installation")
    p.add_argument("--fix", action="store_true",
                   help="regenerate artifacts and relink before diagnosing")
    p.set_defaults(func=cmd_doctor)

    p = sub.add_parser("install", help="source sentinel.conf from your tmux config")
    p.add_argument("--dry-run", action="store_true",
                   help="print the change without making it")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("uninstall", help="remove sentinel from your tmux config")
    p.add_argument("--dry-run", action="store_true",
                   help="print the changes without making them")
    p.add_argument("--keep-options", action="store_true",
                   help="keep options.conf without asking")
    p.add_argument("--purge", action="store_true",
                   help="also delete options.conf")
    p.set_defaults(func=cmd_uninstall)

    p = sub.add_parser("completion", help="emit shell completions")
    p.add_argument("shell", nargs="?", choices=["bash", "zsh"], default="bash")
    p.add_argument("--write", action="store_true",
                   help="regenerate completions/ in the repo")
    p.set_defaults(func=cmd_completion)

    return parser


def main(argv: Optional[List[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args) or 0
    except O.OptionError as exc:
        err(f"sentinel: {exc}")
        return 2
    except O.ConfigError as exc:
        err(f"sentinel: error: {exc}")
        return 1
    except themes.DataFileError as exc:
        err(f"sentinel: error: {exc}")
        return 1
    except O.EngineMissing as exc:
        err(f"sentinel: error: {exc}")
        return 1
    except BrokenPipeError:
        return 0
    except KeyboardInterrupt:
        err("")
        return 130


if __name__ == "__main__":
    sys.exit(main())
