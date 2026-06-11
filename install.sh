#!/bin/bash
# Sable installer for Volumio 4 (Raspberry Pi + Audiophonics EVO Sabre, Quad case).
#
# Mirrors the dependency recipe proven by the quadify plugin on this exact platform
# (32-bit ARM / Bookworm): apt the build toolchain + PREBUILT python3-* C-extension
# packages, then system pip for the pure-Python bits. NO virtualenv (python3-venv
# drags a python3.11 upgrade that breaks dpkg here), and pip never has to compile.
#
# Heals a half-broken apt state first, so it is safe to re-run. NEVER reboots or
# starts the service -- it installs + enables, then tells you to reboot.
#
# Runs standalone (`bash install.sh` as volumio) OR as root when Volumio's plugin
# manager calls it -- sudo is used only when not already root.
set -uo pipefail

SABLE_DIR="${SABLE_DIR:-/home/volumio/sable}"
UNIT_SRC="$SABLE_DIR/systemd/sable.service"
UNIT_DST="/etc/systemd/system/sable.service"
DISABLED_DIR="$SABLE_DIR/disabled-units"
USERCONFIG="/boot/userconfig.txt"
APT_OPTS="-y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold"

SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"

log()  { echo "[sable-install] $*"; }
warn() { echo "[sable-install] WARN: $*" >&2; }

# 0. Preconditions -----------------------------------------------------------
[ -d "$SABLE_DIR/src/sable" ] || { echo "FATAL: Sable code not found at $SABLE_DIR (clone it first)"; exit 1; }
[ -f "$UNIT_SRC" ]           || { echo "FATAL: unit file missing at $UNIT_SRC"; exit 1; }
command -v python3 >/dev/null || { echo "FATAL: python3 not found"; exit 1; }
[ -n "$SUDO" ] && { log "may prompt for the 'volumio' sudo password ..."; sudo -v || { echo "FATAL: sudo required"; exit 1; }; }

# 1. Heal apt + system packages ----------------------------------------------
# Heal any half-configured dpkg state from a previous run, and drop the dead
# nodesource repo that makes `apt-get update` fail on Volumio bookworm.
log "healing apt + installing system packages ..."
$SUDO dpkg --configure -a >/dev/null 2>&1 || true
$SUDO apt-get -f $APT_OPTS install >/dev/null 2>&1 || true
$SUDO rm -f /etc/apt/sources.list.d/nodesource.list \
            /etc/apt/sources.list.d/nodesource.list.save \
            /usr/share/keyrings/nodesource.gpg 2>/dev/null || true
$SUDO apt-get update >/dev/null 2>&1 || warn "apt-get update had errors (continuing)"
# Build toolchain (so pip can compile if needed) + PREBUILT C-extension deps
# (Pillow/cbor2/spidev/RPi.GPIO/smbus) so pip does NOT need to build them -- this
# is what dodges the zlib/Rust/gcc build failures. NO python3-venv (it breaks the
# python3.11 packaging here). Optional: lirc (IR), cava (spectrum), i2c-tools.
$SUDO apt-get install $APT_OPTS --no-install-recommends \
  python3-dev python3-pip build-essential \
  zlib1g-dev libjpeg-dev libfreetype6-dev \
  python3-pil python3-rpi.gpio python3-spidev python3-cbor2 python3-smbus \
  lirc i2c-tools cava \
  || warn "apt install hit problems -- check the package list above"

# 2. Python deps (system pip, pure-Python only; C-ext deps came from apt) -----
log "installing python deps (pip, pure-Python) ..."
$SUDO python3 -m pip install --no-cache-dir --break-system-packages -r "$SABLE_DIR/requirements.txt" \
    || warn "pip install hit problems -- check requirements.txt"
if python3 -c "import luma.oled, PIL, socketio, smbus2" 2>/dev/null; then
    log "python deps OK"
else
    warn "python deps not importable -- Sable will not start until fixed"
fi

# 3. Kernel overlays (written now; take effect on the next REBOOT) -----------
# SPI (OLED), I2C (buttons/LEDs), gpio-ir (IR BCM4). gpio-shutdown wires the EVO
# Sabre power button on BCM17 -- remove that line if your board powers off/won't
# stay up. The EVO Sabre DAC is USB: NO I2S/HAT overlay here.
add_overlay() {
    local line="$1"
    $SUDO touch "$USERCONFIG" 2>/dev/null || true
    if ! grep -qxF "$line" "$USERCONFIG" 2>/dev/null; then
        echo "$line" | $SUDO tee -a "$USERCONFIG" >/dev/null
        log "added to $USERCONFIG: $line"
    fi
}
add_overlay "dtparam=spi=on"
add_overlay "dtparam=i2c_arm=on"
add_overlay "dtoverlay=gpio-ir,gpio_pin=4"
add_overlay "dtoverlay=gpio-shutdown,gpio_pin=17,active_low=1,gpio_pull=up"

# 3b. IR (LIRC): ApEvo remote profile + options + boot hook (only if lirc present).
if [ -d /etc/lirc ]; then
    log "configuring LIRC (ApEvo remote profile) ..."
    $SUDO tee /etc/lirc/lircd.conf        < "$SABLE_DIR/config/lirc/lircd.conf"        >/dev/null
    $SUDO tee /etc/lirc/lirc_options.conf < "$SABLE_DIR/config/lirc/lirc_options.conf" >/dev/null
    $SUDO tee /usr/local/bin/sable-lirc-post.sh < "$SABLE_DIR/bin/sable-lirc-post.sh"  >/dev/null
    $SUDO chmod +x /usr/local/bin/sable-lirc-post.sh
    $SUDO tee /etc/systemd/system/sable-lirc-post.service < "$SABLE_DIR/systemd/sable-lirc-post.service" >/dev/null
    $SUDO systemctl enable lircd.service sable-lirc-post.service >/dev/null 2>&1 || true
else
    warn "LIRC not installed -- skipping IR setup (buttons/rotary still work)"
fi

# 3c. MPD PCM fifo for the spectrum --------------------------------------------
# Sable's cava reads MPD's raw PCM from /tmp/cava.fifo. That fifo is an EXTRA MPD
# output (independent of the real DAC/HDMI output), so the spectrum works on any
# audio device -- but a fresh Volumio has no such output. Add it to the MPD
# TEMPLATE (survives Volumio regenerating mpd.conf) AND the live mpd.conf (takes
# effect now). Skip if any output already targets /tmp/cava.fifo (e.g. quadify's),
# so we never create a duplicate. MPD-routed audio only (local files, web radio);
# AirPlay/Spotify bypass MPD and cannot feed it.
MPD_TMPL="/volumio/app/plugins/music_service/mpd/mpd.conf.tmpl"
MPD_LIVE="/etc/mpd.conf"
read -r -d '' MPD_FIFO_BLOCK <<'EOF'

# --- SABLE_CAVA_FIFO_START ---
audio_output {
    type            "fifo"
    name            "sable_fifo"
    path            "/tmp/cava.fifo"
    format          "44100:16:2"
    always_on       "yes"
    enabled         "yes"
}
# --- SABLE_CAVA_FIFO_END ---
EOF
mpd_fifo_changed=0
for f in "$MPD_TMPL" "$MPD_LIVE"; do
    [ -f "$f" ] || continue
    if grep -q "/tmp/cava.fifo" "$f"; then
        log "MPD fifo output already present in $f -- leaving it"
    else
        printf '%s\n' "$MPD_FIFO_BLOCK" | $SUDO tee -a "$f" >/dev/null
        log "added MPD spectrum fifo output to $f"
        mpd_fifo_changed=1
    fi
done
if [ "$mpd_fifo_changed" -eq 1 ]; then
    $SUDO systemctl restart mpd 2>/dev/null || $SUDO systemctl restart mpd.service 2>/dev/null || true
    log "restarted MPD to enable the spectrum fifo"
fi

# 4. Retire conflicting quadify units IF present (kept, just moved aside) -----
mkdir -p "$DISABLED_DIR"
for u in quadify.service cava.service quadify-buttonsleds.service ir_listener.service; do
    if [ -f "/etc/systemd/system/$u" ]; then
        log "retiring $u -> $DISABLED_DIR/"
        $SUDO systemctl stop "$u"    2>/dev/null || true
        $SUDO systemctl disable "$u" 2>/dev/null || true
        $SUDO mv "/etc/systemd/system/$u" "$DISABLED_DIR/$u"
    fi
done

# 5. Install + enable Sable's boot service (NOT started -- starts on reboot) --
log "installing $UNIT_DST"
$SUDO tee "$UNIT_DST" < "$UNIT_SRC" >/dev/null
$SUDO systemctl daemon-reload
$SUDO systemctl enable sable.service >/dev/null 2>&1 || true

echo
log "DONE. Nothing was started or rebooted."
log "Verify Volumio is still healthy, then:  sudo reboot"
log "(Sable starts automatically on boot. Watch it: journalctl -u sable.service -f)"
