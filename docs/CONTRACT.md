# tmux-sentinel — Design Contract

The interfaces below are what let the pieces of this project stay honest about
each other: the state-file grammar, the engine's CLI, the segment rendering
rules, the tmux option domains, and the theme data format. Change one of them
and you must change every consumer named alongside it. Nothing here is
incidental — each rule exists because its absence produced a real bug.

`test/` enforces most of this. If you are adding a segment, a theme, or an
option, start here and then read [CONTRIBUTING.md](../CONTRIBUTING.md).

## 0. Design thesis

The status bar's right half is rendered by **exactly one implementation**: the native
binary `sentinel-status`. Everything else (preview, TUI, docs) consumes its output.
Configuration lives in **tmux options** (`@sentinel_*`), per TPM ecosystem convention.
Generation from options to on-disk artifacts is done by **exactly one implementation**:
`scripts/generate.sh`. Python is never required for the plugin to function.

Data flow:

```
~/.tmux.conf  set -g @sentinel_theme "nord"
        |
        v
sentinel.tmux  ->  sources options.conf.default (set -ogq) and user options.conf (set -ogq)
        |
        v
scripts/generate.sh  ->  reads @sentinel_* via `tmux show-option -gqv`
        |                 reads themes/<name>.palette + glyphs/<mode>.glyphs
        |
        +--> ~/.config/tmux-sentinel/sentinel.state   (consumed by sentinel-status, per tick)
        +--> ~/.config/tmux-sentinel/sentinel.conf    (sourced by tmux, once)
        |
        v
tmux status-right = #(<bindir>/sentinel-status --clients #{session_attached})
```

## 1. `sentinel.state` file format

Written by `scripts/generate.sh`. Read by `src/sentinel-status.c`.

- UTF-8. LF line endings. Written atomically (temp file + `mv`).
- Lines starting with `#` are comments. Blank lines ignored.
- Every other line is `key=value`. No spaces around `=`. Value is the remainder of the
  line, verbatim, including spaces. No quoting. No escape sequences. No line continuation.
- Keys match `[a-z0-9_]+`. The reader MUST ignore unknown keys (forward compat).
- The writer MUST reject/strip any value containing CR or LF. Values are otherwise opaque.
- Max line length the reader must handle: 512 bytes. Longer lines: skip the line.
- Missing file, or missing key: reader uses its built-in default (Catppuccin Mocha,
  all segments on, the default `always` set, nerd glyphs, default thresholds).
  Reader NEVER fails
  because of a bad state file.

### Complete key list

```
version=1

# Per-segment visibility floor. 1 = render even when the segment has nothing
# to report; 0 = render only when it has crossed a threshold. Segments with no
# quiet state (cpu, memory, clock) render regardless and ignore this.
always_thermal=0
always_sleep_risk=0
always_disk=1
always_battery=0
always_cpu=1
always_memory=1
always_multi_client=0
always_clock=1
clock_format=%H:%M            # strftime, applied by the binary

color_fg=#cdd6f4
color_dim=#6c7086
color_val=#a6adc8
color_sep=#45475a
color_alert=#f38ba8
color_warn=#f9e2af
color_peach=#fab387
color_info=#94e2d5

seg_thermal=1                 # 0|1, one per segment
seg_sleep_risk=1
seg_disk=1
seg_battery=1
seg_cpu=1
seg_memory=1
seg_multi_client=1
seg_clock=1

glyph_sep= ·                  # note: value is " · " -> written as `glyph_sep= · ` (leading+trailing space preserved)
glyph_thermal=<str>
glyph_sleep=<str>
glyph_disk=<str>
glyph_battery_full=<str>
glyph_battery_mid=<str>
glyph_battery_low=<str>
glyph_cpu=<str>
glyph_memory=<str>
glyph_clients=<str>

disk_warn_gb=25               # integers; reader clamps to sane range, ignores non-numeric
disk_crit_gb=15
cpu_warn_pct=70
cpu_crit_pct=90
battery_warn_pct=50
battery_crit_pct=20
```

`glyph_sep` carries its own padding spaces. Since trailing whitespace in a `key=value`
line is fragile under editors, the writer MUST emit it verbatim and the reader MUST NOT
strip trailing whitespace from values. (The reader strips only the terminating LF/CR.)

## 2. `sentinel-status` CLI contract

```
sentinel-status [--state PATH] [--clients N] [--simulate healthy|alert] [--selftest] [--version]
```

- `--state PATH` : override state path. Default
  `${XDG_CONFIG_HOME:-$HOME/.config}/tmux-sentinel/sentinel.state`.
- `--clients N`  : number of attached clients (tmux passes `#{session_attached}`).
  Absent or unparseable => treated as 1.
- `--simulate`   : emit a deterministic bar using the fixed synthetic values in §3
  instead of probing the host. Used by `sentinel preview`, the TUI, and the golden test.
- `--selftest`   : probe every segment, print one human-readable diagnostic line per
  segment to stdout (`name: value | status`), exit 0 if all probes succeeded, 1 otherwise.
  Used by `sentinel doctor`.
- `--version`    : print `sentinel-status <version>` and exit 0.
- Unknown flag   : usage to stderr, exit 2.

Output: the tmux-format status-right fragment on stdout, **no trailing newline**,
**no leading or trailing separator**. Exit 0. Never blocks longer than 100 ms.
On any probe failure the affected segment is omitted; the binary still exits 0.

## 3. Segment rendering rules — normative

Segments render in this fixed order, joined by `#[fg=<color_sep>]<glyph_sep>` placed
**between** rendered segments only (structural join; never leading, never trailing):

`thermal, sleep_risk, disk, battery, cpu, memory, multi_client, clock`

A segment disabled via `seg_*=0` never renders. A segment whose `always_*=0`
renders only when it has something to report; `always_*=1` gives it a visible
resting state. `cpu`, `memory` and `clock` have no resting state to suppress,
so they always render and their `always_*` key is accepted but inert.

| Segment | Renders when | Output |
|---|---|---|
| thermal | pressure above nominal | `#[fg=ALERT]{glyph_thermal} {word}` (Linux: `{n}°C`) |
| thermal | nominal AND `always_thermal=1` | `#[fg=DIM]{glyph_thermal} #[fg=VAL]{word}` |
| sleep_risk | idle sleep armed and no wake assertion held | `#[fg=ALERT]{glyph_sleep} {mins}m` |
| sleep_risk | not at risk AND `always_sleep_risk=1` | `#[fg=DIM]{glyph_sleep} #[fg=VAL]{mins}m`, or `off` when idle sleep is disabled |
| disk | free < `disk_warn_gb`, OR `always_disk=1` | `#[fg=DIM]{glyph_disk} #[fg=C]{gb}G` |
| battery | discharging | `#[fg=C]{icon} {pct}%` |
| battery | not discharging AND `always_battery=1` | `#[fg=DIM]{glyph_battery_full} #[fg=VAL]{pct}%` |
| cpu | always | `#[fg=DIM]{glyph_cpu} #[fg=C]{pct}%` — pct right-aligned width 2 |
| memory | always | `#[fg=DIM]{glyph_memory} #[fg=C]{swap}` e.g. `23.3G` |
| multi_client | clients > 1, OR `always_multi_client=1` | `#[fg=INFO]{glyph_clients} {n}` |
| clock | always | `#[fg=FG,bold]{strftime(clock_format)}` |

Colour selection (`C`):
- disk: `VAL`; `WARN` if `gb < disk_warn_gb`; `ALERT` if `gb < disk_crit_gb`.
- battery (discharging): `WARN` + `glyph_battery_full`; if `pct < battery_warn_pct`
  then `PEACH` + `glyph_battery_mid`; if `pct < battery_crit_pct` then `ALERT` +
  `glyph_battery_low`.
- cpu: `VAL`; `PEACH` if `pct >= cpu_warn_pct`; `ALERT` if `pct >= cpu_crit_pct`.
- memory: `VAL`; `WARN` if pressure level >= 2; `ALERT` if pressure level >= 4.
  (Linux: `WARN` if MemAvailable/MemTotal <= 20%, `ALERT` if <= 10%.)

Thresholds are strict as written above. `disk` uses `<`, `cpu` uses `>=`.

## 4. `--simulate` fixed values — normative, byte-exact

These exist so the golden test can assert the preview equals the engine.

`--simulate healthy`: thermal nominal, sleep risk none (30 minutes armed but an
assertion is held), disk 54 GB, battery 95% not discharging, cpu 22, swap
`23.3G`, memory pressure 1, clients 1, clock renders the literal string `14:30`
(NOT current time — simulate must be deterministic; bypass strftime).

`--simulate alert`: thermal `Fair`, sleep risk armed with 10 minutes, disk 12 GB,
battery 18% discharging, cpu 94, swap `24.1G`, memory pressure 4, clients 2,
clock `14:30`.

Simulate honours the loaded `always_*` flags, so `sentinel preview` shows the
user their own configuration rather than a fixed idea of it. With the shipped
defaults, `--simulate healthy` therefore renders exactly: disk, cpu, memory,
clock.

## 5. tmux options — normative names and defaults

Read with `tmux show-option -gqv <name>`. Defaults live in `options.conf.default`
as `set -ogq` lines so a user's `.tmux.conf` always wins.

| Option | Default | Domain |
|---|---|---|
| `@sentinel_theme` | `catppuccin-mocha` | a filename stem in `themes/` |
| `@sentinel_position` | `top` | `top`\|`bottom` |
| `@sentinel_interval` | `10` | integer 1..3600 |
| `@sentinel_glyphs` | `nerd` | `nerd`\|`unicode`\|`ascii` |
| `@sentinel_always` | `disk,cpu,memory,clock` | comma list, no spaces, drawn from the eight segment names |
| `@sentinel_segments` | `thermal,sleep_risk,disk,battery,cpu,memory,multi_client,clock` | comma list, no spaces |
| `@sentinel_windows` | `hidden` | `hidden`\|`minimal`\|`tabs` |
| `@sentinel_clock_format` | `%H:%M` | strftime; validated against `^[%A-Za-z0-9 :/.,+-]{1,32}$` |
| `@sentinel_session_max_length` | `18` | integer 1..64 |
| `@sentinel_accent` | (from glyph set) | 1..4 chars, no `"` `'` `\` `$` `;` `#` newline |
| `@sentinel_disk_warn_gb` | `25` | integer 0..100000 |
| `@sentinel_disk_crit_gb` | `15` | integer 0..100000 |
| `@sentinel_cpu_warn_pct` | `70` | integer 0..100 |
| `@sentinel_cpu_crit_pct` | `90` | integer 0..100 |
| `@sentinel_battery_warn_pct` | `50` | integer 0..100 |
| `@sentinel_battery_crit_pct` | `20` | integer 0..100 |

**Validation is mandatory and happens in `scripts/generate.sh`.** Any value outside its
domain is replaced by the default AND reported via `tmux display-message`. Nothing
user-controlled is ever interpolated into `sentinel.conf` without domain validation.
This is the fix for the confirmed RCE; treat it as a hard requirement.

`@sentinel_alerts_only` was removed in 0.3.0. It was a single boolean standing in
for eight independent decisions, and it forced disk — the thing people most want
a resting readout of — to be either always hidden or always shown along with
everything else. `@sentinel_always` replaces it and subsumes both of its states:
the old `on` is the shipped default, the old `off` is every segment listed.
`scripts/generate.sh` MUST detect the dead option and say so via
`tmux display-message`, because silently ignoring a setting the user wrote is
the same "knob that lies" defect this project already fixed once.

## 6. Persistence of TUI/CLI edits

`~/.config/tmux-sentinel/options.conf` — lines of the form
`set -ogq @sentinel_theme "nord"`. Written atomically by the Python CLI/TUI.
`sentinel.tmux` sources `options.conf.default` first, then this file, both with
`-ogq`, so explicit user `.tmux.conf` settings always win.
When the CLI/TUI changes a setting it MUST (a) `tmux set-option -g @sentinel_x v` on the
live server, (b) rewrite `options.conf`, (c) run `scripts/generate.sh`, (d) reload.

Migration: if `~/.config/tmux-sentinel/config.json` exists and `options.conf` does not,
convert the JSON to `options.conf` once, then rename the JSON to `config.json.migrated`
and print a one-line notice.

## 7. Theme and glyph data files

`themes/<stem>.palette` — the SOLE definition of a theme. Same `key=value` grammar as
`sentinel.state`. Keys:

```
name=Catppuccin Mocha
description=Smooth dark palette with soothing pastel accents
bg=default
fg=#cdd6f4
dim=#6c7086
val=#a6adc8
sep=#45475a
accent=#89b4fa
prefix=#f38ba8
copy_mode=#f9e2af
warn=#f9e2af
alert=#f38ba8
peach=#fab387
info=#94e2d5
border=#313244
active_border=#89b4fa
message_bg=#313244
mode_bg=#45475a
```

`glyphs/<mode>.glyphs` — same grammar. Keys: `name`, `accent`, `sep`, `thermal`,
`sleep`, `disk`, `battery_full`, `battery_mid`, `battery_low`, `cpu`, `memory`,
`clients`.

Python reads these files; it does NOT hold a second copy of the palettes.
`cli/themes.py` becomes a loader. The old `THEMES` / `GLYPH_SETS` dict literals are deleted.

## 8. Deletions — clean cutover, no shims

Delete outright: `themes/*.conf` (superseded by `.palette`), `scripts/gen_themes.py`,
`cli/config.py`, `cli/generator.py`, and the `env.sh` artifact and every
`SENTINEL_*` environment export. No compatibility aliases, no deprecation paths.
`scripts/status-right.sh` is replaced by `scripts/status-fallback.sh` (see §9).

## 9. Fallback when no compiler is available

`scripts/status-fallback.sh` — POSIX sh, reads `sentinel.state`, renders only
`disk`, `cpu` (loadavg-derived), `memory`, `clock`. Budget: <= 6 forks. It MUST prefix
its output with nothing special, but `sentinel doctor` MUST report that reduced mode is
active. `generate.sh` points `status-right` at the fallback only when the binary is
absent, and re-points at the binary once built.

## 10. Repo invariants

- Every shell script passes `shellcheck` with no warnings (CI enforces).
- `src/sentinel-status.c` compiles clean under `-O2 -Wall -Wextra -Wpedantic -std=c11`.
- No absolute repo paths baked into anything except `sentinel.conf`'s `#()` command,
  which `generate.sh` rewrites on every tmux start (so a moved repo self-heals).
- Python: stdlib only, and only for the CLI and customizer. No new runtime
  dependencies, no `pyproject.toml`. The status bar itself must keep working on
  a machine with no Python at all.
