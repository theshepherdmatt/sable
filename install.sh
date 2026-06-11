#!/bin/bash
# Sable installer for Volumio 4 (Raspberry Pi + Audiophonics EVO Sabre, Quad case).
#
# Works on a FRESH Volumio image AND as a cutover on a unit that still runs the old
# quadify plugin. It:
#   1. installs system + python dependencies (cava, RPi.GPIO, luma, ...),
#   2. enables the kernel overlays Sable needs (SPI, I2C, IR, safe-shutdown),
#   3. retires any conflicting quadify units IF present (kept, just moved aside),
#   4. installs + enables Sable's boot service.
#
# Idempotent: safe to re-run. Assumes the code is already at $SABLE_DIR -- clone it
# first (see README). Run as the `volumio` user:  bash install.sh
#
# sudo note for this platform: systemctl/tee/mv/mkdir/apt/pip are fine via sudo;
# `sudo cp` is NOT reliable (a sudoers line shadows it), so files go in via tee/mv.
set -uo pipefail

SABLE_DIR="${SABLE_DIR:-/home/volumio/sable}"
UNIT_SRC="$SABLE_DIR/systemd/sable.service"
UNIT_DST="/etc/systemd/system/sable.service"
DISABLED_DIR="$SABLE_DIR/disabled-units"
USERCONFIG="/boot/userconfig.txt"

log()  { echo "[sable-install] $*"; }
warn() { echo "[sable-install] WARN: $*" >&2; }

# 0. Preconditions -----------------------------------------------------------
[ -d "$SABLE_DIR/src/sable" ] || { echo "FATAL: Sable code not found at $SABLE_DIR (clone it first)"; exit 1; }
[ -f "$UNIT_SRC" ]           || { echo "FATAL: unit file missing at $UNIT_SRC"; exit 1; }
command -v python3 >/dev/null || { echo "FATAL: python3 not found"; exit 1; }

# 1. System packages ---------------------------------------------------------
# cava            -> spectrum / VU meter feed
# python3-rpi.gpio-> SSD1322 reset pin + rotary GPIO
# lirc            -> IR remote (optional; needs a remote profile to actually work)
# i2c-tools       -> handy for debugging the MCP23017 (i2cdetect)
log "installing system packages (cava, python3-rpi.gpio, lirc, i2c-tools) ..."
sudo apt-get update -y >/dev/null 2>&1 || warn "apt update failed (continuing)"
sudo apt-get install -y cava python3-rpi.gpio lirc i2c-tools \
    || warn "apt install hit problems -- check the packages above"

# 2. Python dependencies (pinned in requirements.txt) ------------------------
log "installing python dependencies ..."
if ! sudo pip3 install --break-system-packages -r "$SABLE_DIR/requirements.txt" >/dev/null 2>&1; then
    sudo pip3 install -r "$SABLE_DIR/requirements.txt" \
        || warn "pip install hit problems -- check requirements.txt"
fi
python3 -c "import luma.oled, PIL, socketio, smbus2" 2>/dev/null \
    || warn "python deps still not importable -- Sable may not start"

# 3. Kernel overlays (need a REBOOT to take effect) --------------------------
# Sable's frozen hardware contract: SPI (OLED), I2C (buttons/LEDs), gpio-ir (IR on
# BCM4), gpio-shutdown (power button on BCM17). The EVO Sabre DAC is USB -- do NOT
# add an I2S/HAT overlay here.
REBOOT_NEEDED=0
add_overlay() {
    local line="$1"
    sudo touch "$USERCONFIG" 2>/dev/null || true
    if ! grep -qxF "$line" "$USERCONFIG" 2>/dev/null; then
        echo "$line" | sudo tee -a "$USERCONFIG" >/dev/null
        log "added to $USERCONFIG: $line"
        REBOOT_NEEDED=1
    fi
}
add_overlay "dtparam=spi=on"
add_overlay "dtparam=i2c_arm=on"
add_overlay "dtoverlay=gpio-ir,gpio_pin=4"
add_overlay "dtoverlay=gpio-shutdown,gpio_pin=17,active_low=1,gpio_pull=up"

# 3b. IR (LIRC): the ApEvo remote profile + daemon options, plus a boot hook that
# neutralises irexec and opens the lircd socket for Sable. Sable's in-process IR
# listener reads /run/lirc/lircd. Different remote? Replace config/lirc/lircd.conf.
log "configuring LIRC (ApEvo remote profile) ..."
sudo tee /etc/lirc/lircd.conf      < "$SABLE_DIR/config/lirc/lircd.conf"      >/dev/null
sudo tee /etc/lirc/lirc_options.conf < "$SABLE_DIR/config/lirc/lirc_options.conf" >/dev/null
sudo tee /usr/local/bin/sable-lirc-post.sh < "$SABLE_DIR/bin/sable-lirc-post.sh" >/dev/null
sudo chmod +x /usr/local/bin/sable-lirc-post.sh
sudo tee /etc/systemd/system/sable-lirc-post.service < "$SABLE_DIR/systemd/sable-lirc-post.service" >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable lircd.service sable-lirc-post.service >/dev/null 2>&1 || true

# 4. Retire conflicting quadify units IF present (kept, just moved aside) -----
# They own the same SPI/GPIO + the PCM fifo reader Sable uses. The quadify plugin
# re-enables some on boot, and they are real files in /etc/systemd/system (so
# `mask` won't hold) -- MOVING the unit files is the only thing that sticks. On a
# fresh image with no quadify, these are simply skipped.
mkdir -p "$DISABLED_DIR"
for u in quadify.service cava.service quadify-buttonsleds.service ir_listener.service; do
    if [ -f "/etc/systemd/system/$u" ]; then
        log "retiring $u -> $DISABLED_DIR/"
        sudo systemctl stop "$u"    2>/dev/null || true
        sudo systemctl disable "$u" 2>/dev/null || true
        sudo mv "/etc/systemd/system/$u" "$DISABLED_DIR/$u"
    fi
done

# 5. Install + enable Sable's boot service -----------------------------------
log "installing $UNIT_DST"
sudo tee "$UNIT_DST" < "$UNIT_SRC" >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable sable.service

if [ "$REBOOT_NEEDED" -eq 1 ]; then
    log "kernel overlays were added -- REBOOT required for SPI/I2C/IR."
    log "run: sudo reboot   (Sable starts automatically on boot)"
else
    sudo systemctl restart lircd.service 2>/dev/null || true
    sudo systemctl restart sable-lirc-post.service 2>/dev/null || true
    sudo systemctl restart sable.service
    sleep 4
    log "sable.service: $(systemctl is-active sable.service) / $(systemctl is-enabled sable.service)"
    log "lircd:         $(systemctl is-active lircd.service 2>/dev/null)"
fi

log "done. Watch it with:  journalctl -u sable.service -f"
log "Web settings editor: install the plugin in $SABLE_DIR/plugin (see plugin/README.md)."
