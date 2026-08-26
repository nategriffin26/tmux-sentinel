# tmux-sentinel — main README demo.
# Render with: vhs assets/demo.tape   (from the repo root)

Output assets/demo.gif

Set Shell "bash"
Set FontFamily "Iosevka Nerd Font Mono"
Set FontSize 20
Set Width 1280
Set Height 300
Set Padding 20
Set Theme "Catppuccin Mocha"
Set TypingSpeed 50ms

Hide
Type "clear && ./assets/demo-session.sh" Enter
Sleep 4s
Type "clear" Enter
Sleep 1.5s
Show

# --- Quiet steady state: CPU, memory, clock. Nothing else. --------------
Sleep 2.5s
Type "git status --short" Sleep 300ms Enter
Sleep 2s
Type "clear" Enter
Sleep 1.5s

# --- Thresholds breached: alerts inject themselves, with numbers --------
Type "# disk filling up, CPU pinned..." Sleep 200ms Enter
Sleep 1s
Type "sentinel set disk_warn_gb 9999" Sleep 200ms Enter
Sleep 2.5s
Type "sentinel set cpu_warn_pct 1" Sleep 200ms Enter
Sleep 3s

# --- Resolved: the bar goes quiet again ---------------------------------
Type "clear && sentinel set disk_warn_gb 25 && sentinel set cpu_warn_pct 70" Sleep 200ms Enter
Sleep 3.5s
