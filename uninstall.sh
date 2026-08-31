#!/bin/sh
set -e
PREFIX="${1:-$HOME/.local}"
rm -rf "$PREFIX/share/tanksmanager"
rm -f "$PREFIX/bin/tanksmanager"
rm -f "$PREFIX/share/applications/de.synthelicz.TanksManager.desktop"
rm -f "$PREFIX/share/metainfo/de.synthelicz.TanksManager.metainfo.xml"
rm -f "$PREFIX/share/icons/hicolor/scalable/apps/de.synthelicz.TanksManager.svg"
echo "Removed Tanks Manager from $PREFIX (config in ~/.config/tanksmanager kept)."
