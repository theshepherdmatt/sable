#!/bin/bash
# Sable installer for moOde 9 (Raspberry Pi). SSD1322 OLED + rotary + buttons/
# LEDs (MCP23017) + optional IR remote.
#
# Mirrors install.sh (the Volumio installer) but talks to MPD directly instead
# of Volumio's Socket.IO API -- see src/sable/moode/listener.py. moOde's
# system user is chosen during moOde's OWN setup (NOT always "pi"), so this
# script detects it at install time instead of hardcoding it.
#
# Heals a half-broken apt state first, so it is safe to re-run. NEVER reboots or
# starts the service -- it installs + enables, then tells you to reboot.
#
# Run as the moOde system user (e.g. `bash install-moode.sh`) OR as root --
# sudo is used only when not already root.
set -uo pipefail

# 0. Figure out who moOde actually runs as -------------------------------
# Prefer the user invoking the script (via sudo or directly); fall back to
# whoever owns /var/lib/mpd (moOde always runs mpd as its own system user,
# so its home directory ownership is a reliable signal even under sudo -u root).
SABLE_USER="${SABLE_USER:-}"
if [ -z "$SABLE_USER" ]; then
    if [ -n "${SUDO_USER:-}" ] && [ "$SUDO_USER" != "root" ]; then
        SABLE_USER="$SUDO_USER"
    elif [ "$(id -un)" != "root" ]; then
        SABLE_USER="$(id -un)"
    else
        SABLE_USER="$(stat -c '%U' /home/*/  2>/dev/null | grep -v '^root$' | head -n1)"
    fi
fi
[ -n "$SABLE_USER" ] || { echo "FATAL: could not determine the moOde system user (set SABLE_USER=... and re-run)"; exit 1; }

# Where the clone actually is. Default to the directory holding THIS script --
# the installer always ships inside the repo, so that is right by construction no
# matter where the user cloned to. (Assuming /home/$SABLE_USER/sable instead just
# hard-failed with "Sable code not found" on any other layout.) An explicit
# SABLE_DIR= still wins.
SABLE_DIR="${SABLE_DIR:-$(cd "$(dirname "$0")" && pwd)}"
UNIT_SRC="$SABLE_DIR/systemd/sable-moode.service.tmpl"
UNIT_DST="/etc/systemd/system/sable.service"
DISABLED_DIR="$SABLE_DIR/disabled-units"
APT_OPTS="-y -o Dpkg::Options::=--force-confdef -o Dpkg::Options::=--force-confold"

SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"

log()  { echo "[sable-install-moode] $*"; }
warn() { echo "[sable-install-moode] WARN: $*" >&2; }

log "moOde system user detected: $SABLE_USER (SABLE_DIR=$SABLE_DIR)"

# 1. Preconditions -------------------------------------------------------
[ -d "$SABLE_DIR/src/sable" ] || { echo "FATAL: Sable code not found at $SABLE_DIR (clone it first)"; exit 1; }
[ -f "$UNIT_SRC" ]           || { echo "FATAL: unit template missing at $UNIT_SRC"; exit 1; }
command -v python3 >/dev/null || { echo "FATAL: python3 not found"; exit 1; }
[ -n "$SUDO" ] && { log "may prompt for the '$SABLE_USER' sudo password ..."; sudo -v || { echo "FATAL: sudo required"; exit 1; }; }

# 2. Heal apt + system packages ------------------------------------------
log "healing apt + installing system packages ..."
$SUDO dpkg --configure -a >/dev/null 2>&1 || true
$SUDO apt-get -f $APT_OPTS install >/dev/null 2>&1 || true
$SUDO apt-get update >/dev/null 2>&1 || warn "apt-get update had errors (continuing)"
# Build toolchain + PREBUILT C-extension deps (Pillow/spidev/smbus) so pip does
# NOT need to compile them. Optional: lirc (IR), i2c-tools. NOTE: mpd is already
# provided by moOde itself. NO cava here -- moOde's spectrum comes from peppyalsa
# (an ALSA plugin every audio_output already routes through by default, writing
# to /tmp/peppyspectrum), which Sable reads directly (fifo_meter.PeppySpectrumBars).
#
# python3-rpi.gpio is DELIBERATELY ABSENT. It is not merely unnecessary on moOde,
# it is UNINSTALLABLE: moode-player depends on boss2-oled-p3, which depends on
# python3-rpi-lgpio, which declares "Conflicts: python3-rpi.gpio". Verified on a
# real moOde 10 (Debian 13) box -- apt refuses with "Reached two conflicting
# decisions". That matters far beyond the one package, because apt aborts the
# WHOLE transaction on an unsatisfiable member: with python3-rpi.gpio in this
# list, lirc / i2c-tools / python3-pil / python3-spidev ALL silently failed to
# install too, and the only visible symptom was a single WARN line. (This is the
# same trap that previously swallowed cava.) RPi.GPIO itself still arrives via
# the pip step below, which pulls it in as a luma.core dependency and compiles it
# from source, independent of apt.
#
# Each package is installed on its own so ONE unavailable package can never again
# take the rest down with it -- the batch form is a single point of failure.
for _pkg in python3-dev python3-pip build-essential \
            zlib1g-dev libjpeg-dev libfreetype6-dev \
            python3-pil python3-spidev python3-cbor2 python3-smbus \
            lirc i2c-tools; do
    $SUDO apt-get install $APT_OPTS --no-install-recommends "$_pkg" >/dev/null 2>&1 \
        || warn "apt could not install '$_pkg' (continuing -- see 'apt-get install $_pkg' for why)"
done

# 3. Python deps (system pip, pure-Python only; C-ext deps came from apt) -----
log "installing python deps (pip, pure-Python) ..."
$SUDO python3 -m pip install --no-cache-dir --break-system-packages \
    -r "$SABLE_DIR/requirements.txt" -r "$SABLE_DIR/requirements-moode.txt" \
    || warn "pip install hit problems -- check requirements.txt / requirements-moode.txt"
if python3 -c "import luma.oled, PIL, mpd" 2>/dev/null; then
    log "python deps OK"
else
    warn "python deps not importable -- Sable will not start until fixed"
fi

# 4. Kernel overlays (written now; take effect on the next REBOOT) -----------
# SPI (OLED), I2C (buttons/LEDs), gpio-ir (IR BCM4). gpio-shutdown wires a safe
# power-off button on BCM17 -- remove that line if your board has nothing on
# BCM17 and powers off / won't stay up. Audio (USB/HAT/HDMI) is moOde's
# concern -- no audio overlay here. On Bookworm+ (moOde 9) the boot partition
# is mounted at /boot/firmware and THAT config.txt is the one the bootloader
# reads -- a stale, inert /boot/config.txt stub can also exist and silently
# eat writes that never take effect (verified on a real moOde 9 Pi 4: SPI/I2C
# never came up after reboot because this picked the stub). Prefer the real
# mounted partition; only fall back to /boot/config.txt if /boot/firmware
# doesn't exist at all (older, non-Bookworm images).
USERCONFIG="/boot/firmware/config.txt"
[ -f "$USERCONFIG" ] || USERCONFIG="/boot/config.txt"
add_overlay() {
    local line="$1"
    if ! grep -qxF "$line" "$USERCONFIG" 2>/dev/null; then
        echo "$line" | $SUDO tee -a "$USERCONFIG" >/dev/null
        log "added to $USERCONFIG: $line"
    fi
}
add_overlay "dtparam=spi=on"
add_overlay "dtparam=i2c_arm=on"
add_overlay "dtoverlay=gpio-ir,gpio_pin=4"
add_overlay "dtoverlay=gpio-shutdown,gpio_pin=17,active_low=1,gpio_pull=up"

# 4b. IR (LIRC): default remote profile + options + boot hook (only if lirc present).
# The default lircd.conf = the SELECTED profile from settings.json (ir.profile),
# default "Xiaomi IR for TV box", taken from the vendored profile library
# (config/lirc/profiles/). The UI picker swaps it later. Falls back to the
# canonical config/lirc/lircd.conf if the profile is missing. lirc_options keeps
# output=/run/lirc/lircd (Sable's reader depends on that socket). NO lircrc/irexec
# is installed -- sable-lirc-post only KILLS irexec and opens the socket.
if [ -d /etc/lirc ]; then
    IR_PROFILE="Xiaomi IR for TV box"
    if [ -f "$SABLE_DIR/config/settings.json" ]; then
        _p="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1])).get("ir",{}).get("profile",""))' \
              "$SABLE_DIR/config/settings.json" 2>/dev/null || true)"
        [ -n "$_p" ] && IR_PROFILE="$_p"
    fi
    IR_CONF="$SABLE_DIR/config/lirc/profiles/$IR_PROFILE/lircd.conf"
    [ -f "$IR_CONF" ] || IR_CONF="$SABLE_DIR/config/lirc/lircd.conf"
    log "configuring LIRC (remote profile: $IR_PROFILE) ..."
    $SUDO tee /etc/lirc/lircd.conf        < "$IR_CONF"                                  >/dev/null
    $SUDO tee /etc/lirc/lirc_options.conf < "$SABLE_DIR/config/lirc/lirc_options.conf" >/dev/null
    $SUDO tee /usr/local/bin/sable-lirc-post.sh < "$SABLE_DIR/bin/sable-lirc-post.sh"  >/dev/null
    $SUDO chmod +x /usr/local/bin/sable-lirc-post.sh
    $SUDO tee /etc/systemd/system/sable-lirc-post.service < "$SABLE_DIR/systemd/sable-lirc-post.service" >/dev/null
    $SUDO systemctl enable lircd.service sable-lirc-post.service >/dev/null 2>&1 || true
else
    warn "LIRC not installed -- skipping IR setup (buttons/rotary still work)"
fi

# 4c. Spectrum: nothing to INSTALL here -- moOde's own peppyalsa ALSA plugin
# writes bars to /tmp/peppyspectrum, which Sable reads directly
# (fifo_meter.PeppySpectrumBars). No MPD fifo output, no cava, nothing to spawn.
#
# But it is NOT on by default, and this script cannot turn it on: moOde ships the
# plugin config as peppy.conf.HIDE and its own worker renames it to peppy.conf
# only when the user enables peppyalsa in moOde's UI. Poking moOde's database
# from here would just be overwritten by that worker. Until it is enabled the
# pipe exists but nothing writes to it, and the visualisers sit flat -- so say so
# rather than let it look like a Sable bug. (Quoode tells its users the same.)
log "NOTE: for the spectrum/VU visualisers, enable peppyalsa in moOde's own UI"
log "      (it is OFF by default -- until then /tmp/peppyspectrum is never written)"

# 5. Retire conflicting Quoode units IF present (kept, just moved aside) -----
mkdir -p "$DISABLED_DIR"
for u in quoode.service cava.service quoode-buttonsleds.service ir_listener.service; do
    if [ -f "/etc/systemd/system/$u" ]; then
        log "retiring $u -> $DISABLED_DIR/"
        $SUDO systemctl stop "$u"    2>/dev/null || true
        $SUDO systemctl disable "$u" 2>/dev/null || true
        $SUDO mv "/etc/systemd/system/$u" "$DISABLED_DIR/$u"
    fi
done

# 5b. Make the clone writable BY the user the unit runs as ------------------
# sable.service runs as $SABLE_USER, and Settings.save() does an atomic write
# (mkstemp + rename) INSIDE config/ -- so a clone owned by anyone else makes the
# app die on startup with "PermissionError: .../config/tmpXXXX.tmp" and then
# crash-loop forever on Restart=always. Seen for real on a box cloned as root
# (git clone under sudo does exactly this), where the ONLY symptom was a restart
# counter climbing past 100. Cheap to assert every run, so assert it.
log "ensuring $SABLE_DIR is owned by $SABLE_USER ..."
$SUDO chown -R "$SABLE_USER" "$SABLE_DIR" || warn "could not chown $SABLE_DIR -- Sable may fail to save settings"

# 6. Install + enable Sable's boot service (NOT started -- starts on reboot) --
# Substitute the detected user/dir into the template -- systemd units can't
# read shell variables, so this is the one place SABLE_USER/SABLE_DIR get baked in.
log "installing $UNIT_DST (user=$SABLE_USER)"
sed -e "s|__SABLE_USER__|$SABLE_USER|g" -e "s|__SABLE_DIR__|$SABLE_DIR|g" "$UNIT_SRC" \
    | $SUDO tee "$UNIT_DST" >/dev/null
$SUDO systemctl daemon-reload
$SUDO systemctl enable sable.service >/dev/null 2>&1 || true

echo
log "DONE. Nothing was started or rebooted."
log "Verify moOde is still healthy, then:  sudo reboot"
log "(Sable starts automatically on boot. Watch it: journalctl -u sable.service -f)"
