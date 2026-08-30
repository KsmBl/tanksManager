"""Map running processes onto icon names from the user's icon theme.

Nothing here hardcodes an icon file - everything is resolved through
GtkIconTheme, so the app picks up whatever the desktop is set to (breeze-dark,
Papirus, Adwaita ...) exactly like Thunar does.
"""

from __future__ import annotations

import configparser
import os
import threading

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

FALLBACK = "application-x-executable"

_XDG_DIRS = None


def _data_dirs():
    global _XDG_DIRS
    if _XDG_DIRS is None:
        home = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
        rest = os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share"
        _XDG_DIRS = [home] + [d for d in rest.split(":") if d]
    return _XDG_DIRS


class IconIndex:
    """exec-basename / StartupWMClass -> icon name, built once in the background."""

    def __init__(self):
        self._map = {}
        self._resolved = {}
        self._ready = threading.Event()
        threading.Thread(target=self._build, daemon=True, name="icon-index").start()

    def _build(self):
        table = {}
        for base in _data_dirs():
            appdir = os.path.join(base, "applications")
            if not os.path.isdir(appdir):
                continue
            for root, _dirs, files in os.walk(appdir):
                for fn in files:
                    if not fn.endswith(".desktop"):
                        continue
                    try:
                        cp = configparser.RawConfigParser(strict=False, interpolation=None)
                        cp.read(os.path.join(root, fn), encoding="utf-8")
                        entry = cp["Desktop Entry"]
                    except (OSError, KeyError, configparser.Error, UnicodeDecodeError):
                        continue
                    icon = entry.get("Icon", "").strip()
                    if not icon:
                        continue
                    keys = set()
                    wmclass = entry.get("StartupWMClass", "").strip()
                    if wmclass:
                        keys.add(wmclass.lower())
                    execline = entry.get("Exec", "").strip()
                    for token in execline.split():
                        if token.startswith("%") or "=" in token:
                            continue
                        cand = os.path.basename(token)
                        if cand and cand not in ("env", "sh", "bash", "flatpak", "gio"):
                            keys.add(cand.lower())
                            break
                    keys.add(os.path.splitext(fn)[0].lower())
                    for key in keys:
                        table.setdefault(key, icon)
        self._map = table
        self._ready.set()

    def icon_for(self, name: str, exe: str = "") -> str:
        """Return an icon name that the current theme can actually render."""
        cached = self._resolved.get(name)
        if cached is not None:
            return cached
        theme = Gtk.IconTheme.get_default()
        candidates = []
        low = name.lower()
        mapped = self._map.get(low)
        if mapped:
            candidates.append(mapped)
        if exe:
            mapped = self._map.get(os.path.basename(exe).lower())
            if mapped:
                candidates.append(mapped)
        candidates.append(low)
        icon = FALLBACK
        for cand in candidates:
            if not cand:
                continue
            if os.path.isabs(cand):
                continue  # absolute paths: not worth a pixbuf load per row
            if theme.has_icon(cand):
                icon = cand
                break
        self._resolved[name] = icon
        return icon

    def invalidate(self):
        self._resolved.clear()


_INDEX = None


def index() -> IconIndex:
    global _INDEX
    if _INDEX is None:
        _INDEX = IconIndex()
    return _INDEX
