# tmux-sentinel — theme showcase still.
# Render with: vhs assets/themes.tape   (from the repo root)
# The GIF output is a throwaway; the artifact is the Screenshot.

Output "/tmp/vhs-themes-throwaway.gif"

Set Shell "bash"
Set FontFamily "Iosevka Nerd Font Mono"
Set FontSize 17
Set Width 1280
Set Height 620
Set Padding 22
Set Theme "Catppuccin Mocha"

Hide
Type "clear && ./assets/theme-grid.sh" Enter
Sleep 7s
Show
Sleep 1s
Screenshot assets/themes.png
Sleep 1s
