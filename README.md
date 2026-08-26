# 󰌘 tmux-sentinel

> **A sleek, native, alerts-only tmux status bar engine with an interactive live-preview TUI customizer.**
> Stays whisper-quiet during healthy steady states; dynamically surfaces actionable alerts (thermal throttling, sleep risks, disk depletion, battery drops, high load) with quantitative metrics.

[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)
[![tmux](https://img.shields.io/badge/tmux-3.0%2B-green.svg)](https://github.com/tmux/tmux)
[![Zero Plugin Runtime](https://img.shields.io/badge/runtime-pure_native-orange.svg)](#architecture)

---

## ⚡ Visual Preview

### 1. Healthy Steady State (Whisper-Quiet)
```text
▌ my-session                                          54G ·  22% · 󰍛 23.3G · 14:30
```
*Clean, uncluttered, focused. Shows only ambient host load and memory/swap.*

### 2. Actionable Alert State (Dynamically Surfaces Risks)
```text
▌ my-session            82% ·  10m ·  12G ·  18% ·  94% · 󰍛 24.1G ·  2 · 14:30
```
*Alert segments inject themselves only when attention is required:*
- ` 82%` — CPU is thermally throttling (speed limit < 100%).
- ` 10m` — System idle-sleep is armed without a wake lock (risks dropping remote mosh/ssh/agent sessions).
- ` 12G` — Data volume critically low on disk space (< 15GB).
- ` 18%` — Battery discharging rapidly (< 20%).
- ` 94%` — CPU load spike (> 90%).
- ` 2` — Multiple clients attached to session.

---

## 🚀 Interactive TUI Customizer

`tmux-sentinel` ships with a built-in terminal UI customizer (`sentinel`). It renders an **exact real-time ANSI preview** of your status bar as you tweak themes, glyphs, and segment toggles, and applies changes to tmux instantly.

```bash
sentinel
```

```text
 󰌘 TMUX-SENTINEL CUSTOMIZER                     Press [s] to Save & Reload | [q] to Exit
╭─ Live Status Bar Preview ─────────────────────────────────────────────────────────────╮
│ ▌ main                                         54G ·  24% · 󰍛 23.3G · 14:30       │
│ [✔ Simulating Healthy State] Theme: Catppuccin Mocha (pos: top)                       │
╰───────────────────────────────────────────────────────────────────────────────────────╯

  🎨 Color Theme             ● Catppuccin Mocha - Smooth dark palette with soothing pastel
  🧩 Health Segments         ○ Tokyo Night - Vibrant night theme inspired by Tokyo neon
  ⚡ Alerts-Only Mode        ○ Nord - Arctic, north-bluish clean aesthetic
  🔤 Glyph Style             ○ Gruvbox Dark - Retro groove warmth with earthy organic tones
  📐 Bar Position & Layout   ○ Rosé Pine - All natural pine, faux fur, and dusky floral rose
  🗂️ Window Tabs Style       ○ Dracula - Famous dark gothic theme with vibrant saturated
  🧪 Test Alert Simulation   ○ Solarized Dark - Precision color system designed for solar
  💾 Save & Apply to Tmux    ○ One Dark - Atom and VS Code iconic balanced dark theme
```

---

## ✨ Features

- **Alerts-Only Philosophy**: Eliminates perpetual widget clutter. Only surfaces metrics when thresholds are breached.
- **Pure-Native Zero-Plugin Overhead**: No heavy ruby/python runtimes or plugin managers running inside statusbar ticks. Execution time is under 15ms.
- **12 Curated Themes**: Catppuccin (Mocha, Macchiato, Frappé, Latte), Tokyo Night, Nord, Gruvbox Dark, Rosé Pine, Dracula, Solarized Dark, One Dark, Monokai Pro.
- **Top / Bottom Placement**: Default top positioning frees the bottom of each terminal pane for AI agent prompts, shell integrations, or clean tiling.
- **Mode Indicator Accent (`▌`)**: Left edge bar dynamically changes color (Blue: Normal, Red: Prefix active, Yellow: Copy-mode).
- **High-Accuracy Mach CPU Tracker**: Includes a lightweight C utility (`mac-cpu-pct`) tracking exact delta CPU ticks between status intervals, with instant POSIX shell fallback.
- **Nerd Font / Unicode / ASCII Glyph Sets**: Seamless support for both full Nerd Font icons and plain ASCII terminals.

---

## 📦 Installation

### Option 1: Automatic 1-Liner (Recommended)

```bash
git clone https://github.com/nategriffin26/tmux-sentinel.git ~/.config/tmux-sentinel/repo
~/.config/tmux-sentinel/repo/bin/sentinel install
```

### Option 2: Tmux Plugin Manager (TPM)

Add to your `~/.tmux.conf`:
```tmux
set -g @plugin 'nategriffin26/tmux-sentinel'
```
Then press `prefix + I` to fetch and activate.

### Option 3: Manual Git Clone & Source

```bash
git clone https://github.com/nategriffin26/tmux-sentinel.git ~/.tmux/plugins/tmux-sentinel
make -C ~/.tmux/plugins/tmux-sentinel
```
Add to your `~/.tmux.conf`:
```tmux
run-shell ~/.tmux/plugins/tmux-sentinel/sentinel.tmux
```

---

## 🛠️ CLI Commands

`sentinel` can also be driven directly from scripts or shell commands:

| Command | Action |
|---|---|
| `sentinel` or `sentinel customize` | Launch the interactive TUI customizer |
| `sentinel preview` | Print a live ANSI status bar preview directly in the terminal |
| `sentinel theme` | List all 12 available themes with color swatches |
| `sentinel theme <name>` | Switch theme and reload tmux live (e.g. `sentinel theme tokyo-night`) |
| `sentinel toggle <segment>` | Toggle segment on/off (`thermal`, `battery`, `cpu`, `disk`, `memory`, `sleep_risk`, `multi_client`, `clock`) |
| `sentinel set position bottom` | Change bar position (`top` / `bottom`) |
| `sentinel set glyph_mode ascii` | Switch icon set (`nerd` / `unicode` / `ascii`) |
| `sentinel apply` | Re-generate configuration files and reload tmux clients |
| `sentinel doctor` | Run system diagnostics and verify dependencies |

---

## 🎨 Themes Included

| Theme Name | Description |
|---|---|
| `catppuccin-mocha` | Soothing dark pastel palette *(default)* |
| `catppuccin-macchiato` | Medium-contrast Catppuccin variant |
| `catppuccin-frappe` | Soft muted dark Catppuccin |
| `catppuccin-latte` | Crisp high-legibility light theme |
| `tokyo-night` | Neon-inspired Tokyo nightscape |
| `nord` | Arctic icy blues and slate grays |
| `gruvbox-dark` | Warm earthy retro groove tones |
| `rose-pine` | Dusky floral roses and pine greens |
| `dracula` | High-contrast saturated dark gothic palette |
| `solarized-dark` | Precision solar-contrast color system |
| `one-dark` | Iconic balanced dark theme |
| `monokai-pro` | Vivid multi-accent modern charcoal theme |

---

## 🧩 Dynamic Segments & Alert Thresholds

| Segment | Icon | Trigger Condition | Thresholds |
|---|---|---|---|
| **Thermal Throttle** | `` | CPU speed limit capped by OS | Red `< 100%` |
| **Sleep Risk** | `` | System idle sleep armed without wake assertion | Red if sleep armed (protects remote mosh/ssh/agents) |
| **Disk Free** | `` | APFS Data container free space | Yellow `< 25GB`, Red `< 15GB` |
| **Battery** | `` / `` | Discharging on battery power | Yellow `< 50%`, Red `< 20%` |
| **CPU Usage** | `` | Real-time delta CPU tick load | Peach `≥ 70%`, Red `≥ 90%` |
| **Memory / Swap** | `󰍛` | Swap usage & kernel VM pressure level | Yellow `Level 2`, Red `Level 4` |
| **Multi-Client** | `` | Active clients attached to session | Cyan if `count > 1` |
| **Clock** | — | System time | Bold foreground `%H:%M` |

---

## 🏗️ Architecture

```text
~/.config/tmux-sentinel/
├── config.json         <-- User settings managed by TUI / CLI
├── sentinel.conf       <-- Static tmux settings (zero parsing latency)
└── env.sh              <-- Pre-computed POSIX shell environment (<1ms load)
```

1. **Static Tmux Configuration (`sentinel.conf`)**: Sourced directly by tmux. Contains native styling, key indicators, and format strings.
2. **Precomputed Shell Environment (`env.sh`)**: When settings or themes change, the CLI generates a 10-line POSIX environment file.
3. **Single-Fork Status Script (`status-right.sh`)**: Executes in a single shell fork per status tick without invoking Python or external runtimes.

---

## 📄 License

MIT © 2026 [Nate Griffin](https://github.com/nategriffin26)
