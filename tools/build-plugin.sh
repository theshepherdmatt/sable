#!/bin/bash
# Assemble the self-contained Sable plugin payload.
#
# Copies the app into plugin/sableapp/ so `cd plugin && volumio plugin install`
# carries everything (Volumio copies the plugin DIR as-is). Keeps src/ canonical:
# plugin/sableapp/ is a build artifact and is gitignored. Run from anywhere.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DST="$ROOT/plugin/sableapp"

echo "Assembling plugin payload -> $DST"
rm -rf "$DST"
mkdir -p "$DST"

# Runtime app + its installer + bundled config defaults/assets. NOT tests, var,
# .git, the plugin folder, or machine-specific config/settings.json|hardware.json.
for item in src config systemd bin assets requirements.txt install.sh; do
    if [ -e "$ROOT/$item" ]; then
        cp -a "$ROOT/$item" "$DST/"
    else
        echo "  WARN: $item not found, skipping"
    fi
done

# Never ship machine-specific runtime state (also gitignored, so usually absent).
rm -f "$DST/config/settings.json" "$DST/config/hardware.json"
# Strip python caches.
find "$DST" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true
find "$DST" -name '*.pyc' -delete 2>/dev/null || true

echo "Done. Files:"
ls "$DST"
echo
echo "Next:  cd $ROOT/plugin && volumio plugin install"
