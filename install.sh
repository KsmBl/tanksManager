#!/bin/sh
# Install Tanks Manager. Defaults to a per-user install under ~/.local,
# which needs no root; pass a prefix for anything else:
#     ./install.sh                 -> ~/.local
#     sudo ./install.sh /usr/local -> system wide
set -e

PREFIX="${1:-$HOME/.local}"
SRC="$(cd "$(dirname "$0")" && pwd)"
LIB="$PREFIX/share/tanksmanager"

echo "Installing to $PREFIX"

python3 - <<'PY'
import sys
try:
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk  # noqa: F401
    import psutil  # noqa: F401
except Exception as exc:
    sys.exit(f"Missing dependency: {exc}\n"
             "Arch:   sudo pacman -S python-gobject gtk3 python-psutil\n"
             "Debian: sudo apt install python3-gi gir1.2-gtk-3.0 python3-psutil\n"
             "Fedora: sudo dnf install python3-gobject gtk3 python3-psutil")
PY

ICONS="$PREFIX/share/icons/hicolor/scalable/apps"
install -d "$LIB" "$PREFIX/bin" "$PREFIX/share/applications" \
           "$PREFIX/share/metainfo" "$ICONS"
rm -rf "$LIB/tanksmanager"
cp -r "$SRC/tanksmanager" "$LIB/tanksmanager"
find "$LIB" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

cat > "$PREFIX/bin/tanksmanager" <<LAUNCHER
#!/bin/sh
exec python3 -c "import sys; sys.path.insert(0, '$LIB'); from tanksmanager.app import main; sys.exit(main())" "\$@"
LAUNCHER
chmod +x "$PREFIX/bin/tanksmanager"

sed "s|^Exec=tanksmanager|Exec=$PREFIX/bin/tanksmanager|" \
    "$SRC/data/de.synthelicz.TanksManager.desktop" \
    > "$PREFIX/share/applications/de.synthelicz.TanksManager.desktop"

install -m 644 "$SRC/data/de.synthelicz.TanksManager.svg" "$ICONS/"
install -m 644 "$SRC/data/de.synthelicz.TanksManager.metainfo.xml" \
        "$PREFIX/share/metainfo/"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$PREFIX/share/applications" 2>/dev/null || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -qtf "$PREFIX/share/icons/hicolor" 2>/dev/null || true
fi

echo "Done. Run 'tanksmanager' (make sure $PREFIX/bin is on your PATH)."
