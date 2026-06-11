# Sable

A front-panel controller for the **Audiophonics EVO Sabre** (Quad case) running
**Volumio 4** on a Raspberry Pi. It drives the unit's SSD1322 OLED, rotary encoder,
front buttons + LEDs, and IR remote -- a ground-up successor to Quadify-Evo.

It is a real, deployed stack: it runs on the Pi as the `sable.service` systemd
unit and owns the display + controls. See `DESIGN.md` for the architecture and
`STATUS.md` for current state.

## What it does

- **16-grey OLED UI** on the 256x64 SSD1322 -- crisp text (rendered bilevel) over
  greyscale album art, gradients and fades.
- **Now-playing** in two themes: **Panel** (album art + info + a dim live spectrum
  floor) and **Cinema** (full-bleed art). A designed **paused** state, never a
  blank panel.
- **Visualisers**: spectrum **Bars / Dots / Mirror / Ribbon**, and an analog
  twin-needle **VU meter** -- all greyscale with peak-hold / VU ballistics.
- **Idle journey**: now-playing -> (paused) -> **clock** -> **dim + pixel-shift**
  (burn-in protection) -> **OLED off**, with a graceful fade and instant wake.
- **Crossfade** transitions between screens; an animated **boot** wordmark.
- **Inputs**: rotary (scroll / press / long-press), 6 front buttons + 7 LEDs
  (MCP23017), and an IR remote (LIRC). One command vocabulary across all three.
- **Web settings**: an optional Volumio plugin (`plugin/`) to edit settings from
  the Volumio UI; `config/settings.json` stays the single source of truth.

## Hardware

Raspberry Pi 4 + Audiophonics EVO Sabre (dual ES9038Q2M, **USB-XMOS** audio -- not
I2S) in a Quad case. Frozen pin/I2C contract lives in `src/sable/hardware.py`:
SSD1322 on SPI0 (RST=BCM24, DC=BCM27, reset/blank=BCM25), rotary BCM13/5/6,
MCP23017 at I2C-1 0x20, IR on BCM4, safe-shutdown on BCM17.

## Install (fresh Volumio)

SSH into the Pi (`ssh volumio@<your-pi>`, default password `volumio`), then:

```bash
# 1. Get the code
git clone https://github.com/theshepherdmatt/sable.git /home/volumio/sable

# 2. Install everything + enable the boot service
cd /home/volumio/sable
bash install.sh

# 3. If install.sh added kernel overlays (SPI/I2C/IR), reboot once
sudo reboot
```

`install.sh` is idempotent and handles a fresh image: it `apt`-installs **cava**,
`python3-rpi.gpio`, `lirc` and `i2c-tools`, `pip`-installs the pinned Python deps
(`requirements.txt`), enables the **SPI / I2C / gpio-ir / gpio-shutdown** kernel
overlays in `/boot/userconfig.txt`, retires any conflicting old quadify units if
present, and installs + enables `sable.service`. After a reboot Sable starts
automatically and brings the panel up.

### Web settings editor (optional)

```bash
cd /home/volumio/sable/plugin && volumio plugin install
```
Then enable **Settings -> Plugins -> Installed Plugins -> Sable**. See
`plugin/README.md` for details.

## Using it

- **Rotary**: turn to scroll, press to select / open the menu, long-press for back
  / home.
- **Menu -> Display Mode** picks the now-playing screen / visualiser; **Brightness**
  and the screensaver timings are there too (and in the web plugin).
- **Buttons** (1-6): play / pause / previous / next / random / repeat. Button 8 is
  the hardware power button (untouched by software).
- **IR**: the **ApEvo** remote profile is installed by `install.sh` and maps
  OK/MENU/arrows/transport. Using a different remote? Replace
  `config/lirc/lircd.conf` (keep the `KEY_*` names) and re-run `install.sh`.

## Updating

```bash
cd /home/volumio/sable && git pull
sudo systemctl restart sable.service     # code changes load on restart
```
Settings changed from the web plugin apply live (no restart). Code changes need a
service restart -- Python loads modules once at startup.

## Debugging

Start here:
```bash
systemctl status sable.service
journalctl -u sable.service -f           # live log (every screen switch, errors)
```

| Symptom | Check |
|---|---|
| **Service won't start** | `journalctl -u sable.service -n 50`. Deps importable? `python3 -c "import luma.oled, PIL, socketio, smbus2"`. SPI present? `ls /dev/spidev0.0`. |
| **Panel blank on cold boot** | Sable pulses the reset line (BCM25) at init; if still blank, `sudo systemctl restart sable.service` (a known fix). Check OLED SPI wiring + that no other process holds SPI. |
| **"Refusing --hardware: quadify.service is ACTIVE"** | The old quadify plugin is fighting for SPI/GPIO. Re-run `install.sh` (it retires those units), or `sudo systemctl stop quadify.service`. |
| **No spectrum / VU needles flat** | `which cava` (must be installed). Is the source MPD-routed? **Radio, Spotify, AirPlay bypass MPD**, so they have no spectrum -- by design. Test with a local track. Check `/tmp/cava.fifo` exists. |
| **Buttons / LEDs dead** | I2C enabled + MCP present? `i2cdetect -y 1` should show `20`. If the bus is missing, controls are disabled (logged). |
| **IR remote does nothing** | `systemctl is-active lircd`; `irw` should print key names (e.g. `KEY_OK`) on a press. `/dev/lirc0` exists only after the gpio-ir overlay + reboot. Different remote -> replace `config/lirc/lircd.conf` and re-run `install.sh`. Buttons/rotary are unaffected. |
| **Clock never appears** | It shows when stopped, or after `screensaver.clock_after_s` (default 300 s) of being paused. Tune it in the web plugin. |
| **Panel dims / sleeps too soon or never** | `screensaver.dim_s` (dim + pixel-shift) and `idle_s` (OLED off). Any input or playback wakes it instantly. `0` disables a tier. |
| **Settings change ignored** | Valid JSON? `cat config/settings.json`. The web plugin pings the app to reload; if the app is down the file still saves -- restart to apply. |
| **Panel upside down** | The contract is `rotate=0` for this unit; override with `--rotate` only if your panel differs. |

Old quadify units retired by the installer are **moved** (not deleted) to
`disabled-units/`; restore them from there if you ever go back.

## Development (no hardware needed)

Sable verifies on a `SimDisplay` backend that renders PNG frames + ASCII previews,
so the whole UI can be exercised on any machine without touching SPI/GPIO:

```bash
PYTHONPATH=src python3 -m sable.app --modern-fixture     # now-playing from a real capture
PYTHONPATH=src python3 -m sable.app --spectrum-fixture   # synthetic spectrum sweep
for t in tests/test_*.py; do python3 "$t"; done          # full self-test suite
python3 tools/ascii_guard.py $(find src tests -name '*.py') plugin/index.js
```

## Layout

```
src/sable/
  hardware.py     frozen hardware contract (pins, I2C, LED bits, DAC labels)
  settings.py     ONE source of truth (config/settings.json), atomic writes
  state.py        PlayerState + StateStore (Volumio quirks normalized once)
  fsm.py          small declarative state machine (state == screen)
  app.py          wiring, idle journey, brightness, crossfade + run modes
  ipc.py          command socket (/tmp/sable-cmd.sock)
  clock_gate.py   NTP boot gate
  display/        base / sim / oled (real SSD1322) / fonts / albumart / icons / fifo_meter
  screens/        base / splash / clock / modern / meter / menu / browse
  inputs/         rotary / buttons (MCP) / ir (LIRC) / sim_input
  volumio/        listener.py -- the single Socket.IO listener (EIO3, queries only)
assets/           vuscreen.png (VU dial) + icons/ (pre-rendered PNGs)
config/           settings.json (live) + .default.json + cava{,-live}.conf
systemd/          sable.service
plugin/           Volumio web-settings plugin (Node)
tools/            ascii_guard / render_icons / test_tone
tests/            pure self-tests (no hardware) + fixtures/
```
