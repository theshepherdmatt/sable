# Sable

A front-panel display + controller for **Volumio 4** and **moOde 9/10** on a
Raspberry Pi. It drives an SSD1322 OLED, a rotary encoder, front buttons + LEDs,
and an IR remote -- a ground-up successor to **Quadify**, installable as a Volumio
plugin or as a plain systemd service on moOde.

It runs on the Pi as the `sable.service` systemd unit and owns the display +
controls; the player stays the player. See `DESIGN.md` for the architecture and
`STATUS.md` for current state.

**Which player you are on changes three things** -- and only these three. Everything
else in this README applies to both:

| | Volumio 4 | moOde 9/10 |
|---|---|---|
| **Player data** | Socket.IO API (`volumio/listener.py`) | MPD directly, port 6600 (`moode/listener.py`) |
| **Spectrum source** | **cava** reading MPD's `/tmp/cava.fifo` (installed by Sable) | moOde's own **peppyalsa** at `/tmp/peppyspectrum` (already there -- nothing to install) |
| **Settings UI** | the Volumio plugin's web page | edit `config/settings.json` (no web page yet) |

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
- **Web settings** (Volumio): the plugin renders a settings page (screen, theme,
  visualiser, brightness, clock, screensaver, controls); `config/settings.json`
  stays the single source of truth and changes apply live. On moOde, edit that
  same file directly -- there is no settings page yet.

## Hardware

Raspberry Pi 4 (Volumio 4 / Bookworm, or moOde 9/10) plus:

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

## Install -- Volumio 4

SSH into the Pi (`ssh volumio@<your-pi>`, default password `volumio`).

### Option A -- as a Volumio plugin (recommended)

Run these as **two separate steps** -- step 2 is interactive, so don't paste it on
the same line as step 1.

**1. Get the code and build the plugin payload:**

```bash
git clone https://github.com/theshepherdmatt/sable.git /home/volumio/sable
cd /home/volumio/sable
bash tools/build-plugin.sh
```

(If your board isn't the standard wiring, also create `config/hardware.json` now --
see Hardware above.)

**2. Install the plugin** (this prompts you to confirm -- answer **Yes**):

```bash
cd /home/volumio/sable/plugin
volumio plugin install
```

This installs **everything** (system deps, kernel overlays, the MPD spectrum fifo,
the boot service) and registers **Sable** under *Settings -> Plugins -> Installed
Plugins*, with its settings page. **Reboot once** afterwards if it added kernel
overlays (SPI/I2C/IR).

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

## Install -- moOde 9/10

moOde has no plugin system, so there is one path: a script that installs a systemd
service. There is **no cava and no MPD fifo** to set up -- moOde's own **peppyalsa**
ALSA plugin already taps every output and writes bars to `/tmp/peppyspectrum`, which
Sable reads directly.

SSH into the Pi. **The username is whatever you chose during moOde's own setup** --
it is *not* always `pi`, and the installer detects it rather than assuming.

```bash
git clone https://github.com/theshepherdmatt/sable.git ~/sable
cd ~/sable
bash install-moode.sh
sudo reboot                # required -- SPI/I2C/IR overlays load at boot
```

The installer never starts anything and never reboots; it installs, enables, and
tells you to reboot. It is idempotent, so re-running it is safe and is the normal
way to re-apply config. It works from wherever you cloned to (it uses its own
location), so `~/sable` is a convention, not a requirement.

After the reboot:

```bash
systemctl status sable.service
journalctl -u sable.service -f
```

A healthy start logs `opening SSD1322 ...` then `moode: connected localhost:6600`.

**Clone as yourself, not as root.** `sudo git clone` leaves the tree root-owned,
and `sable.service` runs as *your* user and must write `config/settings.json` --
so it dies at startup with `PermissionError: .../config/tmpXXXX.tmp` and then
crash-loops silently on `Restart=always`. The installer now `chown`s the tree to
fix this, but a `git pull` under sudo can reintroduce it. If you hit it:

```bash
sudo chown -R $USER ~/sable && sudo systemctl restart sable.service
```

Two moOde-specific notes worth knowing:

- **`python3-rpi.gpio` is uninstallable on moOde** and Sable does not ask for it.
  moOde's `boss2-oled-p3` pulls `python3-rpi-lgpio`, which declares
  `Conflicts: python3-rpi.gpio`. Don't hand-install it -- apt will refuse, and
  because apt aborts a whole transaction on one unsatisfiable package it will
  quietly take out everything else in the same command. Sable gets `RPi.GPIO` from
  pip instead (compiled from source, independent of apt).
- **Samba**, if you use it to edit the source from another machine, is `disabled`
  by default on moOde and will not survive a reboot unless you turn SMB on in
  moOde's own UI (SYSTEM → SMB/NFS) or `sudo systemctl enable smbd`.

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

Pull the latest, then update whichever surface changed.

**Quick code change** — OLED screens / display logic under `src/`. The service runs
from the clone, so a restart reloads it. This is the same on both players (only the
clone path differs — `/home/volumio/sable` on Volumio, `~/sable` on moOde):

```bash
cd ~/sable && git pull
sudo systemctl restart sable.service
```

On **moOde** that is the whole update story — there is no plugin to rebuild. If you
changed anything the installer writes (overlays, LIRC config, the unit itself),
re-run `bash install-moode.sh`; it is idempotent.

**Plugin / settings-page change** (Volumio only) — anything under `plugin/` (`index.js`,
`UIConfig.json`). Volumio serves the plugin from `/data/plugins`, so it has to be
refreshed and the node process restarted:

```bash
cd /home/volumio/sable
git pull
bash tools/build-plugin.sh          # reassemble the plugin payload from src
cd plugin
volumio plugin refresh              # push new plugin files + UIConfig into the system
volumio vrestart                    # restart Volumio so the new index.js runs
sudo systemctl restart sable.service   # reload the display code too
```

`volumio plugin refresh` updates the files and the settings-page layout (UIConfig)
immediately, but a changed `index.js` keeps running the old version until `volumio
vrestart` restarts the node process. Settings changed from the web plugin apply live
(no restart). Code changes need a restart — Python loads modules once at startup.

## Debugging

Start here:
```bash
systemctl status sable.service
journalctl -u sable.service -f           # live log (every screen switch, errors)
```

| Symptom | Check |
|---|---|
| **Service won't start** | `journalctl -u sable.service -n 50`. Deps importable? `python3 -c "import luma.oled, PIL, socketio, smbus2"` (moOde: add `mpd`). SPI present? `ls /dev/spidev0.0` — if missing, the overlays are in `config.txt` but you have not rebooted. |
| **Crash-loops with `PermissionError: .../config/tmpXXXX.tmp`** | The clone is not owned by the user in the unit's `User=` (a `sudo git clone` does this). `sudo chown -R $USER ~/sable && sudo systemctl restart sable.service`. Watch for a climbing `systemctl show sable.service -p NRestarts` — `Restart=always` hides this otherwise. |
| **moOde: overlays added but nothing appears after reboot** | On Bookworm+ the live boot partition is `/boot/firmware/config.txt`. An inert `/boot/config.txt` stub can also exist and silently eat writes. Check the overlays landed in the *firmware* one. |
| **moOde: `apt` says "conflicting decisions" / packages vanish** | `python3-rpi.gpio` conflicts with moOde's `python3-rpi-lgpio` (via `boss2-oled-p3`) and is unsatisfiable. apt aborts the **whole** transaction, so unrelated packages in the same command silently fail too. Never add it; install packages one at a time on moOde. |
| **Panel blank / garbled** | Wrong OLED pins for your board -- set them in `config/hardware.json` (defaults are `DC=24, RST=25, CS=CE0`). On cold boot Sable pulses the reset line; if still blank, `sudo systemctl restart sable.service`. Check nothing else holds SPI. |
| **Plugin install fails at "Installing necessary utilities"** | `journalctl -u volumio --no-pager \| tail -50` -- it runs the plugin `install.sh` (as `sh`). On a *truly* fresh box the apt step can be slow; re-run if it timed out. |
| **No spectrum / VU needles flat** (Volumio) | `which cava` (install with `apt install cava`). MPD must feed `/tmp/cava.fifo` -- the installer adds that output. **Radio via own-player, Spotify, AirPlay bypass MPD**, so they have no spectrum (by design). Test with a local track or standard web radio. |
| **No spectrum / VU needles flat** (moOde) | Nothing to install -- check the pipe exists and is being written: `ls -l /tmp/peppyspectrum` (a `prw-` fifo). If it is missing, moOde's peppyalsa is not in the output chain; confirm moOde routes `pcm._audioout` through `peppy`. Sources that bypass ALSA still have no spectrum. |
| **Buttons / LEDs dead** | I2C enabled + MCP present? `i2cdetect -y 1` should show `20`. If the bus is missing, controls are disabled (logged). |
| **IR remote does nothing** | `systemctl is-active lircd`; `irw` should print key names on a press. `/dev/lirc0` exists only after the gpio-ir overlay + reboot. Different remote -> replace `config/lirc/lircd.conf` and re-run the installer. Buttons/rotary are unaffected. |
| **Album art missing on radio** | Some stations give no art (a vinyl-record placeholder shows). Real station logos load, even with spaces in the filename. |
| **Clock never appears** | It shows when stopped, or after `screensaver.clock_after_s` (default 300 s) paused. Tune it in the web plugin. |
| **Panel dims / sleeps too soon or never** | `screensaver.dim_s` (dim + pixel-shift) and `idle_s` (OLED off). Any input or playback wakes it. `0` disables a tier. |
| **Panel upside down** | In the Sable web plugin -> **Display -> Screen rotation**, choose **Upside-down (180 degrees)**. Sable restarts to apply it. (moOde / headless: set `display.rotate` to `180` in `config/settings.json`, or run with `--rotate 180`.) |

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
  moode/          listener.py -- the MPD listener (python-mpd2, port 6600)
assets/           vuscreen.png (VU dial) + icons/ (pre-rendered PNGs)
config/           settings.json (live) + .default.json + hardware.example.json + cava confs
systemd/          sable.service + sable-moode.service.tmpl (moOde, user/dir substituted at install)
plugin/           Volumio plugin (install + settings UI); tools/build-plugin.sh assembles it
install.sh        Volumio installer   |   install-moode.sh  moOde installer
tools/            build-plugin / ascii_guard / render_icons / test_tone
tests/            pure self-tests (no hardware) + fixtures/
```
