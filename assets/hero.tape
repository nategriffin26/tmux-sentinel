# tmux-sentinel — GitHub social preview card (1280x640).
# Render with: vhs assets/hero.tape   (from the repo root)
# Upload the result at Settings -> General -> Social preview.

Output "/tmp/vhs-hero-throwaway.gif"

Set Shell "bash"
Set FontFamily "Iosevka Nerd Font Mono"
Set FontSize 19
Set Width 1280
Set Height 640
Set Padding 30
Set Theme "Catppuccin Mocha"

Hide
Type "clear && ./assets/hero.sh" Enter
Sleep 5s
Show
Sleep 1s
Screenshot assets/social-preview.png
Sleep 1s
