"""Generate static .conf files for each theme in themes/ directory."""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from cli.themes import THEMES, GLYPH_SETS
THEMES_DIR = REPO_ROOT / "themes"
THEMES_DIR.mkdir(parents=True, exist_ok=True)

glyphs = GLYPH_SETS["nerd"]

for key, theme in THEMES.items():
    conf_path = THEMES_DIR / f"{key}.conf"
    content = f"""# tmux-sentinel theme: {theme['name']}
# Description: {theme['description']}

set -g status-style "bg={theme['bg']},fg={theme['fg']}"

# Mode & Prefix indicator
set -g status-left "#[fg=#{{?client_prefix,{theme['prefix']},#{{?pane_in_mode,{theme['copy_mode']},{theme['accent']}}}}}]{glyphs['accent']}#[fg={theme['fg']},bold] #{{=/18/…:session_name}} #[default]"

# Chrome
set -g message-style "bg={theme['message_bg']},fg={theme['fg']}"
set -g message-command-style "bg={theme['message_bg']},fg={theme['warn']}"
set -g mode-style "bg={theme['mode_bg']},fg={theme['fg']}"
set -g pane-border-style "fg={theme['border']}"
set -g pane-active-border-style "fg={theme['active_border']}"
"""
    conf_path.write_text(content, encoding="utf-8")

print(f"Generated {len(THEMES)} static theme configs in themes/")
