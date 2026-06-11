#!/bin/bash
# Sable settings-bridge plugin install.
# Node dependencies are installed by the Volumio plugin manager from package.json.
# The plugin only reads/writes /home/volumio/sable/config/settings.json and pings
# /tmp/sable-cmd.sock, both owned by the running Sable app -- nothing else to set up.
# (sudo systemctl is NOPASSWD on this unit, so the Restart button needs no extra
# sudoers entry.)
echo "Sable settings bridge: no extra setup required."
echo "plugininstallend"
