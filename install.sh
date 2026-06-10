#!/bin/bash
# Sable installer -- make Sable the live OLED display/control stack on a Quadify
# EVO Sabre unit. Retires quadify's OLED display + CAVA feed, KEEPS quadify's
# buttons/LEDs + IR daemon services (Sable has no controls process yet), and
# installs Sable's own boot service.
#
# Idempotent: safe to re-run (e.g. after a quadify-plugin reinstall recreates the
# units this retires). Assumes the Sable code is already present at $SABLE_DIR
# (this script activates it; it does not fetch it).
#
# sudo note for this Pi: systemctl/tee/mv/mkdir are NOPASSWD; `sudo cp` is NOT
# (a later `(ALL) ALL` sudoers line shadows it). So files go in via `tee`/`mv`.
set -euo pipefail

SABLE_DIR="${SABLE_DIR:-/home/volumio/sable}"
UNIT_SRC="$SABLE_DIR/systemd/sable.service"
UNIT_DST="/etc/systemd/system/sable.service"
DISABLED_DIR="$SABLE_DIR/disabled-units"
CAVA_BIN="/data/plugins/system_hardware/quadify/cava/bin/cava"

log() { echo "[sable-install] $*"; }

# 1. Prerequisites -----------------------------------------------------------
[ -d "$SABLE_DIR/src/sable" ] || { echo "FATAL: Sable code not found at $SABLE_DIR/src/sable"; exit 1; }
[ -f "$UNIT_SRC" ]           || { echo "FATAL: unit file missing at $UNIT_SRC"; exit 1; }
command -v python3 >/dev/null || { echo "FATAL: python3 not found"; exit 1; }
python3 -c "import luma.oled, PIL" 2>/dev/null \
    || { echo "FATAL: python deps missing (need luma.oled + pillow)"; exit 1; }
[ -x "$CAVA_BIN" ] || log "WARN: cava binary not at $CAVA_BIN -- spectrum will be flat until present"

# 2. Retire quadify's conflicting display + CAVA units -----------------------
# They own the same SPI/GPIO (quadify.service) and the same PCM fifo reader
# (cava.service) Sable now uses. The quadify *plugin* re-enables cava.service on
# boot, and these are real files in /etc/systemd/system (so `mask` won't hold);
# the only thing that sticks is MOVING the unit files out.
mkdir -p "$DISABLED_DIR"
# All four are MOVED (not just disabled) because the quadify *plugin* re-enables
# them on boot, so a plain `disable` does not survive a reboot:
#   quadify.service          - old OLED display app (owns SPI/rotary GPIO)
#   cava.service             - old spectrum feed (owns the /tmp/cava.fifo reader)
#   quadify-buttonsleds      - old buttons/LEDs (owns I2C 0x20)  -> Sable owns it now
#   ir_listener.service      - old IR listener (duplicate of Sable's in-process IR)
for u in quadify.service cava.service quadify-buttonsleds.service ir_listener.service; do
    if [ -f "/etc/systemd/system/$u" ]; then
        log "retiring $u -> $DISABLED_DIR/"
        sudo systemctl stop "$u"    2>/dev/null || true
        sudo systemctl disable "$u" 2>/dev/null || true
        sudo mv "/etc/systemd/system/$u" "$DISABLED_DIR/$u"
    else
        log "$u already retired"
    fi
done

# 3. Install + enable Sable's boot service -----------------------------------
log "installing $UNIT_DST"
sudo tee "$UNIT_DST" < "$UNIT_SRC" > /dev/null
sudo systemctl daemon-reload
sudo systemctl enable sable.service
sudo systemctl restart sable.service

# 4. Report ------------------------------------------------------------------
sleep 4
log "sable.service:  $(systemctl is-active sable.service) / $(systemctl is-enabled sable.service)"
log "kept running:   lircd=$(systemctl is-active lircd.service 2>/dev/null) (Sable's IR needs it)"
log "retired:        quadify/cava/buttonsleds/ir_listener (backed up in $DISABLED_DIR)"
log "done -- watch with: journalctl -u sable.service -f"
