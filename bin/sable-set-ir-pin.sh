#!/usr/bin/env bash
# Sable: rewrite the gpio-ir overlay's pin in /boot/userconfig.txt. Run as root
# (via sudo from the plugin's Node backend, see index.js saveIrPin). Takes
# effect on the next reboot, like the rest of userconfig.txt.
#
# Usage: sable-set-ir-pin.sh <bcm-pin 0-27>
set -uo pipefail

USERCONFIG="/boot/userconfig.txt"
PIN="${1:-}"

if ! [[ "$PIN" =~ ^[0-9]+$ ]] || [ "$PIN" -lt 0 ] || [ "$PIN" -gt 27 ]; then
    echo "sable-set-ir-pin: invalid BCM pin '$PIN' (expected 0-27)" >&2
    exit 1
fi

touch "$USERCONFIG" 2>/dev/null || true
if grep -q '^dtoverlay=gpio-ir,gpio_pin=' "$USERCONFIG" 2>/dev/null; then
    sed -i "s/^dtoverlay=gpio-ir,gpio_pin=.*/dtoverlay=gpio-ir,gpio_pin=$PIN/" "$USERCONFIG"
else
    echo "dtoverlay=gpio-ir,gpio_pin=$PIN" >> "$USERCONFIG"
fi
echo "sable-set-ir-pin: gpio-ir pin set to $PIN (reboot to apply)"
