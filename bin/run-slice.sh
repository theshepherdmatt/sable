#!/usr/bin/env bash
# Phase 0 vertical slice -- SIM ONLY. Touches no SPI/I2C/GPIO and no services.
# Safe to run alongside the live plugin.
set -eu
HERE="$(cd "$(dirname "$0")/.." && pwd)"
cd "$HERE"
export PYTHONPATH="$HERE/src"
exec python3 -m sable.app --sim --demo --frames-dir "$HERE/var/frames"
