#!/bin/sh
# Run Tanks Manager straight from the source tree.
cd "$(dirname "$0")" || exit 1
exec python3 -m tanksmanager "$@"
