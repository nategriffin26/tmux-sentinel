"""Main CLI dispatcher for tmux-sentinel."""

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .config import load_config, save_config, CONFIG_DIR, TMUX_CONF_FILE, ENV_FILE
from .themes import THEMES, GLYPH_SETS
from .generator import generate_all, get_repo_dir
from .renderer import render_preview_bar, hex_to_rgb


def cmd_tui(args):
    """Launch the interactive curses TUI customizer."""
    from .tui import start_tui
    start_tui()


def cmd_preview(args):
    """Render a real-time ANSI preview bar in terminal."""
    cfg = load_config()
    if args.theme:
        if args.theme not in THEMES:
            print(f"Error: Unknown theme '{args.theme}'. Available: {', '.join(THEMES.keys())}")
            sys.exit(1)
        cfg["theme"] = args.theme

    width = shutil.get_terminal_size((80, 24)).columns
    session = args.session or "main"

    print("\n" + "=" * width)
    print("  TMUX-SENTINEL LIVE PREVIEW")
    print("=" * width + "\n")

    print("Steady State (Healthy):")
    bar_healthy = render_preview_bar(cfg, width=width, session_name=session, sim_state={
        "thermal": 100,
        "sleep_risk": False,
        "disk_gb": 54,
        "batt_pct": 95,
        "batt_discharging": False,
        "cpu_pct": 22,
        "swap_gb": "23.3G",
        "pressure_level": 1,
        "multi_client": 1,
        "time_str": "14:30",
        "prefix_active": False,
        "in_copy_mode": False,
    })
    print(bar_healthy + "\n")

    print("Alert State (Thermal Throttle, Sleep Risk, Low Disk, Discharging Batt, High CPU):")
    bar_alert = render_preview_bar(cfg, width=width, session_name=session, sim_state={
        "thermal": 82,
        "sleep_risk": True,
        "disk_gb": 12,
        "batt_pct": 18,
        "batt_discharging": True,
        "cpu_pct": 94,
        "swap_gb": "24.1G",
        "pressure_level": 4,
        "multi_client": 2,
        "time_str": "14:30",
        "prefix_active": False,
        "in_copy_mode": False,
    })
    print(bar_alert + "\n")
    print(f"Theme: {THEMES[cfg.get('theme', 'catppuccin-mocha')]['name']} | Glyphs: {cfg.get('glyph_mode', 'nerd')} | Position: {cfg.get('position', 'top')}\n")


def cmd_theme(args):
    """List themes or set the active theme."""
    cfg = load_config()
    if not args.name:
        curr = cfg.get("theme", "catppuccin-mocha")
        print("\nAvailable Themes:")
        print("─────────────────")
        for k, v in THEMES.items():
            marker = " ● (active)" if k == curr else "  "
            # Color swatch preview
            r, g, b = hex_to_rgb(v["accent"])
            swatch = f"\033[38;2;{r};{g};{b}m██\033[0m"
            print(f"{marker} {swatch} {k:<22} ── {v['name']}: {v['description']}")
        print(f"\nRun `sentinel theme <name>` to apply a theme.\n")
        return

    name = args.name.lower()
    if name not in THEMES:
        print(f"Error: Unknown theme '{name}'. Available: {', '.join(THEMES.keys())}")
        sys.exit(1)

    cfg["theme"] = name
    save_config(cfg)
    generate_all(cfg)
    _reload_tmux()
    print(f"✓ Theme set to '{THEMES[name]['name']}' and applied to tmux.")


def cmd_toggle(args):
    """Toggle a status segment on/off."""
    cfg = load_config()
    seg = args.segment.lower()
    segs = cfg.setdefault("segments", {})
    if seg not in segs:
        valid = ", ".join(segs.keys())
        print(f"Error: Unknown segment '{seg}'. Valid segments: {valid}")
        sys.exit(1)

    curr = segs.get(seg, True)
    segs[seg] = not curr
    save_config(cfg)
    generate_all(cfg)
    _reload_tmux()
    print(f"✓ Segment '{seg}' is now {'ENABLED' if not curr else 'DISABLED'} and reloaded.")


def cmd_set(args):
    """Set a configuration value."""
    cfg = load_config()
    key = args.key.lower()
    val = args.value

    if key == "position":
        if val not in ("top", "bottom"):
            print("Error: position must be 'top' or 'bottom'")
            sys.exit(1)
        cfg["position"] = val
    elif key == "glyph_mode":
        if val not in GLYPH_SETS:
            print(f"Error: glyph_mode must be one of: {', '.join(GLYPH_SETS.keys())}")
            sys.exit(1)
        cfg["glyph_mode"] = val
    elif key == "alerts_only":
        cfg["alerts_only"] = val.lower() in ("1", "true", "yes", "on")
    elif key == "interval":
        try:
            cfg["interval"] = int(val)
        except ValueError:
            print("Error: interval must be an integer (seconds)")
            sys.exit(1)
    elif key == "windows_mode":
        if val not in ("hidden", "minimal", "tabs"):
            print("Error: windows_mode must be 'hidden', 'minimal', or 'tabs'")
            sys.exit(1)
        cfg.setdefault("windows", {})["mode"] = val
    else:
        print(f"Error: Unknown setting key '{key}'. (Available: position, glyph_mode, alerts_only, interval, windows_mode)")
        sys.exit(1)

    save_config(cfg)
    generate_all(cfg)
    _reload_tmux()
    print(f"✓ Set {key} = {val} and reloaded tmux.")


def cmd_get(args):
    """Print configuration as JSON."""
    cfg = load_config()
    if args.key:
        print(json.dumps(cfg.get(args.key), indent=2))
    else:
        print(json.dumps(cfg, indent=2))


def cmd_apply(args):
    """Regenerate configs from JSON and reload tmux."""
    cfg = load_config()
    generate_all(cfg)
    _reload_tmux()
    print("✓ tmux-sentinel: Configuration regenerated and tmux reloaded live.")


def cmd_generate(args):
    """Generate configuration files without reloading tmux."""
    cfg = load_config()
    generate_all(cfg)
    print(f"✓ Generated {TMUX_CONF_FILE} and {ENV_FILE}")


def cmd_doctor(args):
    """Run diagnostic checks on environment and setup."""
    print("\n🔍 TMUX-SENTINEL DOCTOR")
    print("───────────────────────")

    # Tmux check
    tmux_path = shutil.which("tmux")
    if tmux_path:
        ver = subprocess.run(["tmux", "-V"], capture_output=True, text=True).stdout.strip()
        print(f"  ✓ tmux found: {ver} ({tmux_path})")
    else:
        print("  ✗ tmux not found in PATH")

    # OS check
    os_name = subprocess.run(["uname", "-s"], capture_output=True, text=True).stdout.strip()
    print(f"  ✓ Operating System: {os_name}")

    # C helper check
    repo_dir = get_repo_dir()
    bin_cpu = repo_dir / "bin" / "mac-cpu-pct"
    local_cpu = Path.home() / ".local" / "bin" / "mac-cpu-pct"
    if bin_cpu.exists() and os.access(bin_cpu, os.X_OK):
        print(f"  ✓ mac-cpu-pct binary found in repo: {bin_cpu}")
    elif local_cpu.exists() and os.access(local_cpu, os.X_OK):
        print(f"  ✓ mac-cpu-pct binary found in ~/.local/bin: {local_cpu}")
    elif os_name == "Darwin":
        print("  ! mac-cpu-pct binary not compiled (will use sysctl fallback). Run `make -C src` to build.")

    # Config files check
    print(f"  ✓ Config directory: {CONFIG_DIR}")
    print(f"    - config.json:  {'[FOUND]' if (CONFIG_DIR / 'config.json').exists() else '[NOT CREATED - using defaults]'}")
    print(f"    - sentinel.conf: {'[FOUND]' if TMUX_CONF_FILE.exists() else '[NOT CREATED - run `sentinel generate`]'}")
    print(f"    - env.sh:        {'[FOUND]' if ENV_FILE.exists() else '[NOT CREATED - run `sentinel generate`]'}")

    # tmux.conf check
    tmux_conf = Path.home() / ".tmux.conf"
    if tmux_conf.exists():
        content = tmux_conf.read_text(encoding="utf-8")
        if "sentinel" in content or "statusbar.conf" in content:
            print(f"  ✓ ~/.tmux.conf has statusbar integration")
        else:
            print(f"  ! ~/.tmux.conf does not source sentinel.conf. Run `sentinel install` to link.")
    print()


def cmd_install(args):
    """Link sentinel.conf into ~/.tmux.conf."""
    cfg = load_config()
    generate_all(cfg)

    tmux_conf = Path.home() / ".tmux.conf"
    source_line = f"source-file {TMUX_CONF_FILE}"

    if tmux_conf.exists():
        content = tmux_conf.read_text(encoding="utf-8")
        if source_line in content or str(TMUX_CONF_FILE) in content:
            print(f"✓ ~/.tmux.conf already sources {TMUX_CONF_FILE}")
            _reload_tmux()
            return

        # Replace old statusbar.conf line if present
        if "source-file ~/.config/tmux/statusbar.conf" in content:
            content = content.replace("source-file ~/.config/tmux/statusbar.conf", source_line)
            tmux_conf.write_text(content, encoding="utf-8")
            print(f"✓ Replaced old statusbar.conf with {source_line} in ~/.tmux.conf")
        else:
            with open(tmux_conf, "a", encoding="utf-8") as f:
                f.write(f"\n# tmux-sentinel status bar\n{source_line}\n")
            print(f"✓ Added '{source_line}' to ~/.tmux.conf")
    else:
        tmux_conf.write_text(f"# tmux-sentinel status bar\n{source_line}\n", encoding="utf-8")
        print(f"✓ Created ~/.tmux.conf with '{source_line}'")

    _reload_tmux()
    print("✓ Installation complete! Tmux status bar is active.")


def _reload_tmux():
    try:
        if TMUX_CONF_FILE.exists():
            subprocess.run(["tmux", "source-file", str(TMUX_CONF_FILE)], capture_output=True)
            subprocess.run(["tmux", "refresh-client", "-S"], capture_output=True)
    except Exception:
        pass


def main():
    parser = argparse.ArgumentParser(
        prog="sentinel",
        description="tmux-sentinel: Minimal, alerts-only tmux status bar customizer and engine."
    )
    subparsers = parser.add_subparsers(dest="command", help="Subcommand to run")

    # TUI
    sub_tui = subparsers.add_parser("customize", aliases=["tui"], help="Open interactive TUI customizer")
    sub_tui.set_defaults(func=cmd_tui)

    # Preview
    sub_prev = subparsers.add_parser("preview", help="Render ANSI preview of status bar in terminal")
    sub_prev.add_argument("--theme", "-t", help="Theme to preview")
    sub_prev.add_argument("--session", "-s", help="Simulated session name")
    sub_prev.set_defaults(func=cmd_preview)

    # Theme
    sub_thm = subparsers.add_parser("theme", aliases=["themes"], help="List or set theme")
    sub_thm.add_argument("name", nargs="?", help="Theme name to apply")
    sub_thm.set_defaults(func=cmd_theme)

    # Toggle
    sub_tog = subparsers.add_parser("toggle", help="Toggle a status segment")
    sub_tog.add_argument("segment", help="Segment name (thermal, sleep_risk, disk, battery, cpu, memory, multi_client, clock)")
    sub_tog.set_defaults(func=cmd_toggle)

    # Set
    sub_set = subparsers.add_parser("set", help="Set a configuration option")
    sub_set.add_argument("key", help="Setting key (position, glyph_mode, alerts_only, interval, windows_mode)")
    sub_set.add_argument("value", help="Value to set")
    sub_set.set_defaults(func=cmd_set)

    # Get
    sub_get = subparsers.add_parser("get", help="Get configuration JSON or key value")
    sub_get.add_argument("key", nargs="?", help="Key to inspect")
    sub_get.set_defaults(func=cmd_get)

    # Apply / Reload
    sub_app = subparsers.add_parser("apply", aliases=["reload"], help="Regenerate configs and live reload tmux")
    sub_app.set_defaults(func=cmd_apply)

    # Generate
    sub_gen = subparsers.add_parser("generate", help="Generate config files without reloading tmux")
    sub_gen.set_defaults(func=cmd_generate)

    # Doctor
    sub_doc = subparsers.add_parser("doctor", help="Run system diagnostics")
    sub_doc.set_defaults(func=cmd_doctor)

    # Install
    sub_inst = subparsers.add_parser("install", help="Link sentinel into ~/.tmux.conf")
    sub_inst.set_defaults(func=cmd_install)

    args = parser.parse_args()

    if not args.command:
        # Default with no args: launch TUI if interactive terminal, else show preview
        if sys.stdin.isatty():
            cmd_tui(args)
        else:
            cmd_preview(args)
    else:
        args.func(args)


if __name__ == "__main__":
    main()
