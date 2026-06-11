#!/bin/bash
# Sable installer for Volumio 4 (Raspberry Pi + Audiophonics EVO Sabre, Quad case).
#
# SAFE BY DESIGN: it never reboots, never starts services, and installs Python deps
# into an ISOLATED virtualenv (so it cannot disturb Volumio's own system Python).
# After it finishes the box is unchanged at runtime; you review, then reboot.
#
# Works on a fresh Volumio image AND as a cutover from the old quadify plugin.
# Idempotent. Clone the code to $SABLE_DIR first, then run as `volumio`:
#     bash install.sh
#
# sudo note: systemctl/tee/mv/mkdir/apt are fine via sudo; `sudo cp` is NOT
# reliable here (a sudoers line shadows it), so files go in via tee/mv.
set -uo pipefail

SABLE_DIR="${SABLE_DIR:-/home/volumio/sable}"
VENV="$SABLE_DIR/.venv"
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
# Cache sudo credentials once (Volumio may prompt for the volumio password). This
# lets the rest of the script's sudo calls run without per-command prompts -- some
# of which are output-redirected and would otherwise fail silently.
log "Sable installer -- you may be asked for the 'volumio' password (for sudo)."
sudo -v || { echo "FATAL: sudo is required"; exit 1; }

# 1. System packages (minimal, tolerant -- never abort the box) --------------
# Essential: python3-venv (build the isolated env) + python3-rpi.gpio (OLED reset
# pin + rotary). Optional: cava (spectrum), lirc (IR), i2c-tools (debug). We do
# NOT upgrade anything -- only install these, with --no-install-recommends.
log "installing system packages ..."
sudo apt-get update -y >/dev/null 2>&1 || warn "apt update failed (continuing with cached lists)"
sudo apt-get install -y --no-install-recommends python3-venv python3-rpi.gpio \
    || warn "ESSENTIAL apt packages failed (python3-venv / python3-rpi.gpio)"
sudo apt-get install -y --no-install-recommends cava lirc i2c-tools \
    || warn "optional apt packages failed (cava/lirc) -- spectrum/IR may be unavailable"

# 2. Python deps in an ISOLATED virtualenv -----------------------------------
# --system-site-packages so the venv sees apt's RPi.GPIO, but pip installs
# luma/Pillow/socketio ONLY into the venv -- Volumio's system Python is never
# touched (this is the safety fix; system-wide pip could clobber Volumio's deps).
log "creating virtualenv at $VENV ..."
if [ ! -x "$VENV/bin/python" ]; then
    python3 -m venv --system-site-packages "$VENV" || warn "venv creation failed (need python3-venv)"
fi
if [ -x "$VENV/bin/pip" ]; then
    "$VENV/bin/pip" install --disable-pip-version-check -q -r "$SABLE_DIR/requirements.txt" \
        || warn "pip install into venv hit problems -- check requirements.txt"
    if "$VENV/bin/python" -c "import luma.oled, PIL, socketio, smbus2" 2>/dev/null; then
        log "python deps OK in venv"
    else
        warn "venv deps not importable -- Sable will not start until fixed"
    fi
else
    warn "no venv -- skipping pip (Sable will not start without it)"
fi

# 3. Kernel overlays (written now; only take effect on the next REBOOT) -------
# SPI (OLED), I2C (buttons/LEDs), gpio-ir (IR on BCM4). gpio-shutdown wires the
# EVO Sabre power button on BCM17 -- if your board has nothing on BCM17 and the
# unit powers off/won't stay up, remove that line from $USERCONFIG. The EVO Sabre
# DAC is USB: do NOT add any I2S/HAT overlay here.
add_overlay() {
    local line="$1"
    sudo touch "$USERCONFIG" 2>/dev/null || true
    if ! grep -qxF "$line" "$USERCONFIG" 2>/dev/null; then
        echo "$line" | sudo tee -a "$USERCONFIG" >/dev/null
        log "added to $USERCONFIG: $line"
    fi
}
add_overlay "dtparam=spi=on"
add_overlay "dtparam=i2c_arm=on"
add_overlay "dtoverlay=gpio-ir,gpio_pin=4"
add_overlay "dtoverlay=gpio-shutdown,gpio_pin=17,active_low=1,gpio_pull=up"

# 3b. IR (LIRC): the ApEvo remote profile + options + a boot hook (only if lirc
# installed). Sable's in-process listener reads /run/lirc/lircd. Swap the remote
# by replacing config/lirc/lircd.conf.
if [ -d /etc/lirc ]; then
    log "configuring LIRC (ApEvo remote profile) ..."
    sudo tee /etc/lirc/lircd.conf        < "$SABLE_DIR/config/lirc/lircd.conf"        >/dev/null
    sudo tee /etc/lirc/lirc_options.conf < "$SABLE_DIR/config/lirc/lirc_options.conf" >/dev/null
    sudo tee /usr/local/bin/sable-lirc-post.sh < "$SABLE_DIR/bin/sable-lirc-post.sh"  >/dev/null
    sudo chmod +x /usr/local/bin/sable-lirc-post.sh
    sudo tee /etc/systemd/system/sable-lirc-post.service < "$SABLE_DIR/systemd/sable-lirc-post.service" >/dev/null
    sudo systemctl enable lircd.service sable-lirc-post.service >/dev/null 2>&1 || true
else
    warn "LIRC not installed -- skipping IR setup (buttons/rotary still work)"
fi

# 4. Retire conflicting quadify units IF present (kept, just moved aside) -----
mkdir -p "$DISABLED_DIR"
for u in quadify.service cava.service quadify-buttonsleds.service ir_listener.service; do
    if [ -f "/etc/systemd/system/$u" ]; then
        log "retiring $u -> $DISABLED_DIR/"
        sudo systemctl stop "$u"    2>/dev/null || true
        sudo systemctl disable "$u" 2>/dev/null || true
        sudo mv "/etc/systemd/system/$u" "$DISABLED_DIR/$u"
    fi
done

# 5. Install + enable Sable's boot service (NOT started -- starts on reboot) --
log "installing $UNIT_DST"
sudo tee "$UNIT_DST" < "$UNIT_SRC" >/dev/null
sudo systemctl daemon-reload
sudo systemctl enable sable.service >/dev/null 2>&1 || true

echo
log "DONE. Nothing was started or rebooted -- the box is unchanged at runtime."
log "Verify Volumio is still healthy, then load the overlays + start Sable with:"
log "    sudo reboot"
log "(Sable starts automatically on boot. Watch it: journalctl -u sable.service -f)"
log "To try Sable WITHOUT rebooting first (SPI/I2C need the overlays, so this may"
log "fail until you reboot):  sudo systemctl start sable.service"
