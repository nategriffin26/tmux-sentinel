# Changelog

All notable changes to tmux-sentinel are documented here. This project follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and uses semantic versioning.

## [0.3.0] - 2026-08-26

Free disk space is on the bar by default, and every segment's resting visibility is now controlled individually.

### Added

- `@sentinel_always` — a comma-separated list of the segments that render even when they have nothing to report. Default `disk,cpu,memory,clock`. Each of the eight segments can be given or denied a resting state independently.
- `sentinel always <segment>` flips one segment's resting state, mirroring the existing `sentinel toggle` for enablement, so turning one thing on does not mean retyping the whole list.
- The `sleep_risk` segment gained a resting form: the idle-sleep timer when nothing is at risk, or `off` when idle sleep is disabled. Previously it could only appear as an alert.
- The `multi_client` segment gained a resting form, so the client count can be shown permanently rather than only above one.
- The customizer's old boolean "steady state" category is now a per-segment list; `cpu`, `memory` and `clock` are labelled inert rather than silently ignoring a toggle.

### Changed

- **Free disk space is shown by default.** It previously stayed hidden until it fell below `disk_warn_gb`, which meant the number you most want at a glance was the one you could not see.
- `--simulate` now honours the configured `always_*` flags, so `sentinel preview` and the customizer show your configuration rather than a fixed idea of it.

### Removed

- `@sentinel_alerts_only`. One boolean stood in for eight independent decisions, and it forced disk to be either always hidden or shown alongside everything else. Both of its states remain expressible: the old `on` is the shipped default, the old `off` is every segment named in `@sentinel_always`. A still-set `@sentinel_alerts_only` is reported via `tmux display-message` rather than silently ignored, and the legacy `config.json` migration converts it.

## [0.2.0] - 2026-08-26

Version 2 is a clean rewrite around one native renderer and validated tmux options.

### Fixed

- TPM and manual installs no longer produce an empty status bar: the old entrypoint's `python3 cli/main.py` call raised an import error that was suppressed, while its fallback referenced a file that was never created.
- `make install-bin` now installs a launcher that can import its own package.
- Malformed configuration is no longer silently replaced by defaults and overwritten; writes are atomic.
- `reload` now reports tmux configuration errors and preserves tmux's failure status.
- Status bars no longer receive a trailing separator when the clock segment is disabled.
- Preview width now counts double-width glyphs correctly instead of wrapping to a second line.
- Concurrent CPU samples no longer corrupt readings (the old 160-sample repro returned 142 empty results and otherwise noisy 0–100% values).
- The thermal segment works on Apple Silicon: it uses thermal-pressure notifications instead of relying on the speed limit that `pmset -g therm` does not report there.

### Security

- Removed arbitrary shell and tmux command execution through unescaped configuration values. `clock_format` could previously inject complete tmux commands with a newline; configuration now lives in validated tmux options.
- Removed the predictable `/tmp` CPU state file, which had no `O_NOFOLLOW` or ownership check and enabled local file clobbering. State now uses a validated per-user runtime directory with locking.

### Performance

- The status engine is now a single native binary with **zero** child processes per tick. Interleaved A/B on an M3 Pro, stable across repeated runs: **5.4 ms median versus v0.1's 44.6 ms — an 8.2x speedup**. About 1.7 ms of what remains is process-spawn cost that any `#()` command pays. v0.1 forked 21 times on a healthy tick and 28 on an alert tick, dominated by four `pmset` invocations at 7-12 ms each. Reproduce with `make bench`.

### Changed

- Configuration moved from `~/.config/tmux-sentinel/config.json` to `@sentinel_*` tmux options, with one-time migration on first run.

### Removed

- `env.sh`
- `scripts/gen_themes.py`
- Generated `themes/*.conf`
- `cli/config.py`
- `cli/generator.py`

[0.3.0]: https://github.com/nategriffin26/tmux-sentinel/releases/tag/v0.3.0
[0.2.0]: https://github.com/nategriffin26/tmux-sentinel/releases/tag/v0.2.0
