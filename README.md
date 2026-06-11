# Sable

A front-panel display + controller for **Volumio 4** on a Raspberry Pi. It drives
an SSD1322 OLED, a rotary encoder, front buttons + LEDs, and an IR remote -- a
ground-up successor to **Quadify**, installable as a Volumio plugin.

It runs on the Pi as the `sable.service` systemd unit and owns the display +
controls; Volumio stays the player. See `DESIGN.md` for the architecture and
`STATUS.md` for current state.

## What it does

- **16-grey OLED UI** on the 256x64 SSD1322 -- crisp text (rendered bilevel) over
  greyscale album art, gradients and fades.
- **Now-playing** in two themes: **Panel** (album art + info + a dim live spectrum
  floor) and **Cinema** (full-bleed art). A designed **paused** state, never a
  blank panel. A **source tag** (LIBRARY / TIDAL / QOBUZ / RADIO / AIRPLAY / ...).
- **Visualisers**: spectrum **Bars / Dots / Mirror / Ribbon**, and an analog
  twin-needle **VU meter** -- all greyscale with peak-hold / VU ballistics.
- **Idle journey**: now-playing -> (paused) -> **clock** -> **dim + pixel-shift**
  (burn-in protection) -> **OLED off**, with a graceful fade and instant wake.
- **Crossfade** transitions between screens; an animated **boot** wordmark.
- **Inputs**: rotary (scroll / press / long-press), 6 front buttons + 7 LEDs
  (MCP23017), and an IR remote (LIRC). One command vocabulary across all three.
- **Web settings**: the Volumio plugin renders a settings page (screen, theme,
  visualiser, brightness, clock, screensaver, controls); `config/settings.json`
  stays the single source of truth and changes apply live.

## Hardware

Raspberry Pi 4 (Volumio 4 / Bookworm) plus:

- **SSD1322 256x64 OLED** on SPI0
- **rotary encoder** (CLK/DT/SW)
- **MCP23017** I2C expander -- 6 buttons + 7 LEDs
- optional **IR receiver** on a GPIO

The default pin/I2C contract (the **standard Quadify wiring**) lives in
`src/sable/hardware.py`: OLED on SPI0 (**CS=CE0/GPIO8, DC=GPIO24, RST=GPIO25**),
rotary BCM13/5/6, MCP23017 at I2C-1 `0x20`, IR on BCM4, optional safe-shutdown
button on BCM17.

**Board wired differently?** Drop a `config/hardware.json` overriding only the
pins you changed -- see `config/hardware.example.json` (which happens to be the
Audiophonics EVO Sabre wiring: `DC=27, RST=24`). It's a per-machine file, not a
user setting, and never appears in the UI. The DAC is irrelevant to the wiring --
audio can be USB, HAT/I2S or HDMI.

## Install

SSH into the Pi (`ssh volumio@<your-pi>`, default password `volumio`).

### Option A -- as a Volumio plugin (recommended)

```bash
git clone https://github.com/theshepherdmatt/sable.git /home/volumio/sable
cd /home/volumio/sable
bash tools/build-plugin.sh          # assembles the plugin payload
cd plugin && volumio plugin install
```

This installs **everything** (system deps, kernel overlays, the MPD spectrum fifo,
the boot service) and registers **Sable** under *Settings -> Plugins -> Installed
Plugins*, with its settings page. Reboot once if it added kernel overlays
(SPI/I2C/IR). If your board isn't the standard wiring, create
`config/hardware.json` first (see Hardware above).

### Option B -- manual / development

```bash
cd /home/volumio/sable
bash install.sh        # same setup, no plugin wrapper
sudo reboot            # once, to load the SPI/I2C/IR overlays
```

Either path runs the same idempotent installer. On 32-bit ARM it `apt`-installs a
build toolchain plus prebuilt `python3-pil` / `python3-cbor2` / `python3-spidev` /
`python3-rpi.gpio` (so nothing has to compile), then `pip`-installs the pure-Python
deps; enables the **SPI / I2C / gpio-ir / gpio-shutdown** overlays in
`/boot/userconfig.txt`; adds an MPD **fifo output** (`/tmp/cava.fifo`) that feeds
the spectrum on any audio device; and installs the service. For the spectrum/VU
visualisers, **cava** is used (`sudo apt install cava` -- it pulls some GUI libs).

## Using it

- **Rotary**: turn to scroll, press to select / open the menu, long-press for back
  / home.
- **Buttons** (1-6): play / pause / previous / next / random / repeat.
- **Web settings**: the plugin's page picks the now-playing screen / visualiser,
  theme, brightness, clock and screensaver timings -- applied live.
- **IR**: an example remote profile (`ApEvo`) is bundled and maps
  OK/MENU/arrows/transport. Using a different remote? Replace
  `config/lirc/lircd.conf` (keep the `KEY_*` names) and re-run the installer.

## Updating

Plugin install: rebuild + reinstall the plugin (`bash tools/build-plugin.sh` then
`volumio plugin install`), or for a quick code change:

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
| **Panel blank / garbled** | Wrong OLED pins for your board -- set them in `config/hardware.json` (defaults are `DC=24, RST=25, CS=CE0`). On cold boot Sable pulses the reset line; if still blank, `sudo systemctl restart sable.service`. Check nothing else holds SPI. |
| **Plugin install fails at "Installing necessary utilities"** | `journalctl -u volumio --no-pager \| tail -50` -- it runs the plugin `install.sh` (as `sh`). On a *truly* fresh box the apt step can be slow; re-run if it timed out. |
| **No spectrum / VU needles flat** | `which cava` (install with `apt install cava`). MPD must feed `/tmp/cava.fifo` -- the installer adds that output. **Radio via own-player, Spotify, AirPlay bypass MPD**, so they have no spectrum (by design). Test with a local track or standard web radio. |
| **Buttons / LEDs dead** | I2C enabled + MCP present? `i2cdetect -y 1` should show `20`. If the bus is missing, controls are disabled (logged). |
| **IR remote does nothing** | `systemctl is-active lircd`; `irw` should print key names on a press. `/dev/lirc0` exists only after the gpio-ir overlay + reboot. Different remote -> replace `config/lirc/lircd.conf` and re-run the installer. Buttons/rotary are unaffected. |
| **Album art missing on radio** | Some stations give no art (a vinyl-record placeholder shows). Real station logos load, even with spaces in the filename. |
| **Clock never appears** | It shows when stopped, or after `screensaver.clock_after_s` (default 300 s) paused. Tune it in the web plugin. |
| **Panel dims / sleeps too soon or never** | `screensaver.dim_s` (dim + pixel-shift) and `idle_s` (OLED off). Any input or playback wakes it. `0` disables a tier. |
| **Panel upside down** | Add `"oled": {"rotate": 180}` to `config/hardware.json`. |

Old quadify units, if present, are **moved** (not deleted) to `disabled-units/` by
the installer; restore them from there if you ever go back.

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
  hardware.py     pin/I2C contract (standard wiring) + config/hardware.json overrides
  settings.py     ONE source of truth (config/settings.json), atomic writes
  state.py        PlayerState + StateStore (Volumio quirks normalized once)
  fsm.py          small declarative state machine (state == screen)
  app.py          wiring, idle journey, brightness, crossfade + run modes
  ipc.py          command socket (/tmp/sable-cmd.sock)
  display/        base / sim / oled (real SSD1322) / fonts / albumart / icons / fifo_meter
  screens/        base / splash / clock / modern / meter / menu / browse
  inputs/         rotary / buttons (MCP) / ir (LIRC) / sim_input
  volumio/        listener.py -- the single Socket.IO listener (EIO3, queries only)
assets/           vuscreen.png (VU dial) + icons/ (pre-rendered PNGs)
config/           settings.json (live) + .default.json + hardware.example.json + cava confs
systemd/          sable.service
plugin/           Volumio plugin (install + settings UI); tools/build-plugin.sh assembles it
tools/            build-plugin / ascii_guard / render_icons / test_tone
tests/            pure self-tests (no hardware) + fixtures/
```
