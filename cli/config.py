"""Configuration loader and manager for tmux-sentinel."""

import json
import os
import copy
from pathlib import Path
from typing import Dict, Any

CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))) / "tmux-sentinel"
CONFIG_FILE = CONFIG_DIR / "config.json"
ENV_FILE = CONFIG_DIR / "env.sh"
TMUX_CONF_FILE = CONFIG_DIR / "sentinel.conf"

DEFAULT_CONFIG: Dict[str, Any] = {
    "theme": "catppuccin-mocha",
    "position": "top",
    "interval": 10,
    "glyph_mode": "nerd",
    "alerts_only": True,
    "segments": {
        "thermal": True,
        "sleep_risk": True,
        "disk": True,
        "battery": True,
        "cpu": True,
        "memory": True,
        "multi_client": True,
        "clock": True,
    },
    "left": {
        "show_session_name": True,
        "max_session_length": 18,
        "prefix_indicator": True,
        "accent_symbol": "▌",
    },
    "windows": {
        "mode": "hidden",   # "hidden" (zen/agent focus) | "minimal" | "tabs"
        "active_style": "bold",
    },
    "clock_format": "%H:%M",
    "thresholds": {
        "disk_warn_gb": 25,
        "disk_crit_gb": 15,
        "cpu_warn_pct": 70,
        "cpu_crit_pct": 90,
        "battery_warn_pct": 50,
        "battery_crit_pct": 20,
    }
}


def load_config() -> Dict[str, Any]:
    """Load configuration from disk, falling back to default."""
    if not CONFIG_FILE.exists():
        return copy.deepcopy(DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            user_cfg = json.load(f)
        cfg = copy.deepcopy(DEFAULT_CONFIG)
        _deep_update(cfg, user_cfg)
        return cfg
    except Exception:
        return copy.deepcopy(DEFAULT_CONFIG)


def save_config(cfg: Dict[str, Any]) -> None:
    """Save configuration to disk."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def _deep_update(base: Dict[str, Any], overrides: Dict[str, Any]) -> None:
    for k, v in overrides.items():
        if k in base and isinstance(base[k], dict) and isinstance(v, dict):
            _deep_update(base[k], v)
        else:
            base[k] = v
