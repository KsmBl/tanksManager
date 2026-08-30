#!/bin/sh
set -e
PREFIX="${1:-$HOME/.local}"
rm -rf "$PREFIX/share/tanksmanager"
rm -f "$PREFIX/bin/tanksmanager"
rm -f "$PREFIX/share/applications/de.synthelicz.TanksManager.desktop"
echo "Removed Tanks Manager from $PREFIX (config in ~/.config/tanksmanager kept)."
