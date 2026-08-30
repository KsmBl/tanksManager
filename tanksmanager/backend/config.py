"""Persisted settings (~/.config/tanksmanager/config.json)."""

from __future__ import annotations

import json
import os

CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config"),
    "tanksmanager",
)
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

DEFAULTS = {
    "update_speed": "normal",          # high | normal | low | paused
    "tab": 1,                          # tab index restored on start
    "always_on_top": False,
    "hide_when_minimised": False,
    "minimise_on_use": False,
    "classic_graphs": True,            # XP green-on-black; off = follow the theme
    "single_click": False,             # follow Thunar's single-click activation
    "tree_view": False,
    "all_users": True,
    "kernel_threads": False,           # kworker/ksoftirqd and friends
    "cpu_per_core_scale": False,       # show CPU as sum over cores (htop style)
    "one_graph_per_cpu": True,     # View > CPU History > One Graph Per CPU
    "show_kernel_times": True,     # red kernel time under green user time
    "confirm_kill": True,
    "window": [760, 560],
    "maximised": False,
    "columns": None,                   # list of visible process column ids
    "sort": ["cpu", "desc"],
    "perf_card": "cpu",            # which Performance card is selected
    "perf_sidebar": 250,           # width of the Performance card strip
}


class Config(dict):
    def __init__(self):
        super().__init__(DEFAULTS)
        self.load()

    def load(self):
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                for key, value in data.items():
                    if key in DEFAULTS:
                        self[key] = value
        except (OSError, ValueError):
            pass

    def save(self):
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            tmp = CONFIG_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(dict(self), fh, indent=2, sort_keys=True)
            os.replace(tmp, CONFIG_FILE)
        except OSError:
            pass


UPDATE_SPEEDS = {
    "high": 0.5,
    "normal": 1.0,
    "low": 4.0,
    "paused": 3600.0,
}
