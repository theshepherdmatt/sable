#!/bin/bash
# Sable plugin install -- run as root by Volumio's plugin manager.
#
# This is the WHOLE Sable install. It deploys the bundled app (sableapp/, assembled
# by tools/build-plugin.sh) to /home/volumio/sable, then runs the app's own proven
# installer (the quadify-recipe: apt build deps + system pip, kernel overlays, MPD
# spectrum fifo, LIRC, the systemd service). Node deps come from package.json via
# the plugin manager. Settings/wiring files already on disk are preserved.
set -uo pipefail

PLUGIN_DIR="$(cd "$(dirname "$0")" && pwd)"
APP_SRC="$PLUGIN_DIR/sableapp"
APP_DST="/home/volumio/sable"

echo "Sable plugin: deploying app to $APP_DST ..."
if [ ! -d "$APP_SRC/src/sable" ]; then
    echo "FATAL: bundled app missing ($APP_SRC). Run tools/build-plugin.sh before packaging."
    echo "plugininstallend"
    exit 1
fi
mkdir -p "$APP_DST"
# Copy the app in. sableapp/ deliberately contains NO config/settings.json or
# config/hardware.json, so an existing machine's settings/wiring are not clobbered.
cp -a "$APP_SRC/." "$APP_DST/"
chown -R volumio:volumio "$APP_DST"

echo "Sable plugin: running the app installer ..."
chmod +x "$APP_DST/install.sh"
( cd "$APP_DST" && SABLE_DIR="$APP_DST" bash install.sh ) \
    || echo "Sable plugin: app installer reported issues (see log above)"

echo "Sable plugin: install complete. Use the Sable settings page to configure."
echo "plugininstallend"
