# Contributing to tmux-sentinel

Thanks for improving tmux-sentinel. Keep changes small, test the actual tmux integration, and preserve the security boundaries below.

## Development setup

The project has no runtime dependencies beyond a C compiler, POSIX shell tools, Python 3 (stdlib only), and tmux.

```sh
make
python3 -m compileall cli/
```

Builds must be clean under `-O2 -Wall -Wextra -Wpedantic`. Run the focused engine checks directly:

```sh
./bin/sentinel-status --simulate healthy
./bin/sentinel-status --simulate alert
```

Use a private server while developing. This does not touch your normal tmux session or configuration:

```sh
tmux -L dev -f /dev/null start-server
tmux -L dev set-option -g @sentinel_theme nord
XDG_CONFIG_HOME=/tmp/tmux-sentinel-dev scripts/generate.sh
tmux -L dev -f /dev/null source-file /tmp/tmux-sentinel-dev/tmux-sentinel/sentinel.conf
tmux -L dev kill-server
```

Run `sentinel doctor` when diagnosing probes or reduced fallback mode. Do not use the real `~/.config/tmux-sentinel` or `~/.tmux.conf` for tests.

## Architecture

The data flow is **tmux options -> `scripts/generate.sh` -> `sentinel.state` -> `bin/sentinel-status`**: `sentinel.tmux` loads defaults and user options, the generator validates those options and writes the state/config artifacts, and the native binary is the one renderer for the status bar. Preview and diagnostics consume that same renderer; there must be exactly one bar-rendering implementation.

All user-controlled values must be domain-validated before they reach `sentinel.conf`. Never interpolate an unchecked option into a tmux command, shell fragment, or sourced file; reject or replace invalid values with the documented default.

## Adding a theme

Add one file at `themes/<name>.palette`. The filename stem is the value of `@sentinel_theme`. Read `themes/catppuccin-mocha.palette` first; use the existing `key=value` grammar, LF endings, and no quoting. A palette must define every key below:

- `name`: display name
- `description`: short description
- `bg`: tmux background colour
- `fg`: foreground colour
- `dim`: muted foreground colour
- `val`: normal value colour
- `sep`: segment separator colour
- `accent`: accent colour
- `prefix`: tmux prefix colour
- `copy_mode`: copy-mode colour
- `warn`: warning colour
- `alert`: alert colour
- `peach`: high-but-not-critical colour
- `info`: informational colour
- `border`: pane border colour
- `active_border`: active pane border colour
- `message_bg`: message background colour
- `mode_bg`: mode indicator background colour

Keep values valid for tmux colour options. Do not add generated `.conf` files; the palette is the sole theme definition.

## Adding a segment

A segment requires all of the following in `src/sentinel-status.c`:

1. A `sentinel.state` key and built-in default (unknown keys remain forward-compatible).
2. A bounded, failure-safe host probe that does not block beyond the CLI contract.
3. A renderer entry in the fixed segment order, using the normative colour and separator rules.
4. A deterministic value for both `--simulate healthy` and `--simulate alert`, with the golden output remaining stable.

Update the relevant contract-facing tests and documentation when the observable state grammar changes. Keep the separator structural: it belongs only between rendered segments.

## Pull requests

Explain the user-visible behavior and security impact. Include the OS, tmux version, and commands used for verification. Before submitting, run ShellCheck on all `*.sh` files and `sentinel.tmux`, build with warnings enabled, generate a configuration using a private tmux socket, and source that configuration successfully.
