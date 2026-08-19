# Sable -- Overview (for an AI repair agent)

Read this first. It assumes zero prior context about this project.

## What this is

"Sable" is a front-panel display + controller application for Raspberry Pi
music-streaming boxes. It draws an OLED UI (clock, now-playing, menus,
spectrum/VU visualisers) and reads a rotary encoder, front-panel buttons +
LEDs, and an IR remote. It is the successor to an earlier project called
**Quadify** -- you will see "quadify" referenced throughout the code and
service files as the predecessor whose services Sable retires/replaces
(`README.md:5`, `STATUS.md:9`, `src/sable/hardware.py` comments).

Sable is **not** the music player. It sits on top of one of two player
backends:

- **Volumio 4** -- talks to Volumio over Socket.IO (`src/sable/volumio/listener.py`)
- **moOde 9/10** -- talks to MPD directly on port 6600 (`src/sable/moode/listener.py`)

Which backend is active is chosen by the `SABLE_PLATFORM` environment
variable (`volumio` default, or `moode`), read in `src/sable/app.py:869`
(`_platform()`).

Sable ships as:
- a **Volumio plugin** (`plugin/` directory, installed via `volumio plugin install`), or
- a **plain systemd service** on moOde (`install-moode.sh` + `systemd/sable-moode.service.tmpl`)

Both paths ultimately run the same Python application
(`src/sable/app.py`) as the `sable.service` systemd unit
(`systemd/sable.service`).

## Hardware stack

Documented in `README.md:40-58` and `src/sable/hardware.py:1-30`, cross-
checked against the official project site, quadify.uk (checked 2026-08-19 --
see the citation note at the bottom of this file):

- **Raspberry Pi 4 Model B, 4GB** -- the only Pi model referenced in the
  repo (per source: `README.md:42`, `src/sable/hardware.py` comments; not
  stated in code as the *only* supported model, but it's the only one
  named). Matches quadify.uk's stated recommended/tested base hardware (per
  quadify.uk/shopping.html), though the 4GB RAM figure specifically is a
  quadify.uk claim only -- the code/README never mention a RAM size.
- **SSD1322 256x64 OLED** display, driven over SPI0 via the `luma.oled`
  library (per source: `src/sable/display/oled.py`). Matches quadify.uk's
  stated 2.8-inch SSD1322 SPI OLED (per quadify.uk/oled.html,
  quadify.uk/shopping.html). Exact SPI pin numbers are given in code (see
  `docs/ai-repair/GPIO-MAP.md`); quadify.uk's own wiring diagram for this is
  image-based and doesn't give pin numbers in extractable text.
- **Rotary encoder** (CLK/DT/SW pins), read via `RPi.GPIO` polling, wired
  directly to Pi GPIO -- NOT through the MCP23017 (per source:
  `src/sable/inputs/rotary.py`, `src/sable/hardware.py:33-42`). This matches
  quadify.uk's description of the rotary as a module with "DT, CLK, and SW
  connect[ed] to individual Raspberry Pi pins" (per quadify.uk/wiring.html).
  quadify.uk names the module as a **KY-040** rotary encoder; the code itself
  never names a specific part number, only the CLK/DT/SW pin contract, so the
  KY-040 identification is site-only, not verified in code. Exact BCM pins
  (default CLK=13, DT=5, SW=6) are code-only -- quadify.uk's wiring diagram
  is an image and gives no pin numbers in extractable text.
- **MCP23017 I2C GPIO expander** at I2C address `0x20` (auto-probed
  `0x20`-`0x27` if not overridden) driving 6-8 front-panel buttons and
  7-8 status LEDs (per source: `src/sable/inputs/buttons.py`,
  `src/sable/controls.py`). This is a **separate I2C device from the rotary
  encoder** -- confirmed in code: buttons live on the MCP's GPIOB (a 2x4
  matrix) and LEDs on GPIOA, both reached only via I2C (SDA/SCL), while the
  rotary's CLK/DT/SW are direct Pi GPIO reads (see previous bullet). This
  matches quadify.uk's description of the MCP23017 wiring: buttons on rows
  B0-B7, LEDs on rows A0-A7, with the chip itself reaching the Pi via just
  4 wires (power, ground, SDA, SCL) (per quadify.uk/mcp23017.html). Neither
  the code nor quadify.uk gives exact BCM numbers for the SDA/SCL wires --
  on a Pi 4 these are conventionally the fixed I2C-1 pins GPIO2/GPIO3, but
  that is inferred from standard Raspberry Pi hardware, not stated
  explicitly by either source -- verify with `raspi-gpio get` or `i2cdetect`
  if in doubt.
- **Optional IR receiver** on a GPIO pin (default BCM4), read via the LIRC
  daemon (per source: `src/sable/inputs/ir.py`). Not mentioned on
  quadify.uk's hardware pages reviewed for this pass.
- **DAC / audio output is explicitly NOT Sable's concern in the code** --
  audio can be USB DAC, an I2S HAT, or HDMI; Sable "adds no audio overlay"
  (per source: `src/sable/hardware.py:110-111`, `README.md:57`). quadify.uk
  states a **HiFiBerry DAC+** as the recommended/default DAC HAT. Per the
  maintainer (2026-08-19), the actual reason is specific, not general "GPIO
  compatibility": the HiFiBerry DAC+ does not use the I2C bus (GPIO2/SDA,
  GPIO3/SCL), so it never contends with the MCP23017 expander that drives
  Sable's buttons and LEDs over I2C. It's still worth flagging that nothing
  in this repo's code, README, or install scripts (`install.sh`,
  `install-moode.sh`) references HiFiBerry, DAC+, or installs/configures any
  specific DAC overlay -- no conflict was found against Sable's own default
  pins on either I2S or I2C (see `docs/ai-repair/GPIO-MAP.md`'s DAC conflict
  section), but that's the maintainer's stated reasoning, not something this
  repo's code itself verifies or enforces. Any DAC HAT pin usage is
  independent of, but can physically overlap, Sable's own pins -- see
  `docs/ai-repair/GPIO-MAP.md`.

There are **two known physical wiring variants** in this codebase:
1. **Standard "Quadify" wiring** -- the DEFAULT pin values in
   `src/sable/hardware.py` (OLED DC=GPIO24, RST=GPIO25).
2. **"EVO Sabre" wiring** -- an older unit variant wired DC=GPIO27, RST=GPIO24.
   Documented via `config/hardware.example.json` and the comments in
   `src/sable/hardware.py:3-6`.

Per-machine wiring differences are NOT hard-coded and NOT user settings --
they live in a gitignored `config/hardware.json` file that overrides only
the pins that differ from the standard defaults (`src/sable/hardware.py:72-102`).

## How the pieces connect (software)

Entry point: `src/sable/app.py`, function `main()` (line 1323) and
`run_hardware()` (line 1054), which is what `sable.service` actually runs
(`--hardware --stage full`, see `systemd/sable.service:20`).

```
sable.service (systemd)
  -> python3 -m sable.app --hardware --stage full
       -> App (src/sable/app.py) -- the central object. Owns:
            - self.display   : OledDisplay (src/sable/display/oled.py) -- real SPI panel
                                or SimDisplay (src/sable/display/sim.py) -- PNG/ASCII bench backend
            - self.store     : StateStore (src/sable/state.py) -- ONE normalized player state,
                                fed by whichever listener is active
            - self.fsm       : FSM (src/sable/fsm.py) -- screen state machine; "state == screen"
            - self.settings  : Settings (src/sable/settings.py) -- config/settings.json, the
                                single source of truth for USER preferences
            - self.listener  : VolumioListener or MoodeListener -- live connection to the player
       -> starts input threads (only in --stage full):
            - RotaryEncoder  (src/sable/inputs/rotary.py)   -- GPIO polling thread
            - IrListener     (src/sable/inputs/ir.py)       -- reads LIRC's unix socket
            - CommandServer  (src/sable/ipc.py)             -- /tmp/sable-cmd.sock, for scripting
            - ButtonsLeds    (src/sable/inputs/buttons.py)  -- MCP23017 I2C scan + LED drive thread
       -> ALL input sources funnel into ONE command vocabulary: App.handle(cmd, arg)
            (src/sable/app.py:548) -- this is the single place button/rotary/IR/IPC commands
            are interpreted, so behaviour is consistent regardless of input source.
       -> screens (src/sable/screens/*.py) each implement render(canvas, draw, w, h) and are
          swapped by the FSM based on player state (playing/paused/stopped) and user navigation.
       -> a background render "tick" thread (see run_hardware's tick_loop, app.py:1107) calls
          App.render() at a fixed FPS (default 20), which paints the CURRENT screen to the OLED.
```

### Key module responsibilities

| File | Responsibility |
|---|---|
| `src/sable/app.py` | Central wiring: App class, command dispatch, render loop, idle/screensaver ladder, shutdown sequence, hardware bring-up (`run_hardware`) |
| `src/sable/hardware.py` | The pin/I2C **wiring contract** (BCM pin numbers) -- NOT user-editable; see GPIO-MAP.md |
| `src/sable/settings.py` | User-editable preferences, `config/settings.json`, atomic writes |
| `src/sable/state.py` | `StateStore` -- normalizes raw player events into one `PlayerState` |
| `src/sable/fsm.py` | Screen state machine (which screen is "current") |
| `src/sable/display/oled.py` | Real SSD1322 OLED driver over SPI (luma.oled) |
| `src/sable/display/sim.py` | Headless bench display backend (PNG + ASCII), used for dev/tests |
| `src/sable/display/base.py` | Abstract `Display` interface shared by both backends |
| `src/sable/display/fifo_meter.py` | Reads the spectrum/VU data feed (CAVA fifo on Volumio, peppyalsa pipe on moOde) |
| `src/sable/inputs/rotary.py` | Rotary encoder quadrature decode + GPIO polling driver |
| `src/sable/inputs/buttons.py` | MCP23017 button matrix scan + LED drive (Sable's own controller, replaces Quadify's) |
| `src/sable/inputs/ir.py` | LIRC socket listener, remote-key -> command mapping |
| `src/sable/controls.py` | A SEPARATE, simpler MCP23017 daemon (`sable-controls`) -- see note below |
| `src/sable/screens/*.py` | One file per UI screen (clock, modern now-playing, menu, browse, meter/spectrum, shutdown, splash, home) |
| `src/sable/volumio/listener.py` | Volumio Socket.IO client |
| `src/sable/moode/listener.py` | moOde MPD client |
| `src/sable/ipc.py` | Local command socket for scripting/testing (`/tmp/sable-cmd.sock`) |
| `plugin/` | Volumio plugin wrapper (Node.js `index.js`, `UIConfig.json`, `plugin.json`) that lets Volumio's web UI manage install/settings |
| `install.sh` / `install-moode.sh` | Installers -- apt/pip deps, kernel overlays, systemd units, sudoers rule |

**Note on two button/LED controllers**: the repo contains BOTH
`src/sable/controls.py` (a standalone `sable-controls` daemon with its own
systemd template `systemd/sable-controls.service.tmpl`) AND
`src/sable/inputs/buttons.py` (`ButtonsLeds`, started in-process by
`run_hardware` under `--stage full`). Per `STATUS.md` and `systemd/sable.service`,
the **in-process `ButtonsLeds` in `inputs/buttons.py` is the one actually
running in production** (started from `app.py:1247-1257`). `controls.py`
appears to be an earlier/alternate standalone daemon -- **flag this to the
user if diagnosing button/LED issues**: confirm which one is actually
enabled (`systemctl status sable-controls.service` vs. checking that
`sable.service`'s `--stage full` buttons thread started) before editing.
(inferred from code, please confirm)

## Data flow (a button press, end to end)

1. `ButtonsLeds._scan_loop()` (`src/sable/inputs/buttons.py:181`) polls the
   MCP23017 button matrix over I2C every `debounce_s` (default 0.1s).
2. On a press edge it calls `self._on_press(btn)` which resolves the button
   number to a `(cmd, arg)` pair -- either the hard-coded default in
   `_BUTTON_ACTION` or a user override from `settings.json` (`buttons.btn_N`).
3. It calls `self.handle(cmd, arg)`, which is `App.handle` -- the SAME
   entry point used by rotary/IR/IPC.
4. `App.handle` interprets the command (transport, volume, menu nav, etc.)
   and typically calls into the current screen's `handle_scroll` /
   `handle_select` / `handle_back`, or sends a command to the active
   listener (Volumio/moOde), then calls `App.render()`.
5. `App.render()` (`app.py:414`) asks the FSM's current screen to paint
   itself onto a PIL canvas, then hands that to `self.display.present()`
   (SPI blit on real hardware).

## Boot sequence (real hardware)

`run_hardware()` in `app.py` (line 1054):
1. Refuses to start if the old `quadify.service` (or `quoode.service` on
   moOde) is still active, to avoid contending for the same SPI/GPIO
   (`app.py:1065-1070`).
2. Opens the OLED (`OledDisplay`), shows a splash screen.
3. Starts the Volumio/moOde listener.
4. Waits (`boot_gate_s`, default 120s) for BOTH the system clock to be
   NTP-synced (`src/sable/clock_gate.py`) AND the player backend to be
   reachable, before showing the clock -- so it never shows a wrong time or
   a falsely "ready" UI.
5. In `--stage full` (production): starts the live CAVA spectrum source (or
   moOde's peppyalsa reader), the rotary encoder, the IR listener, the IPC
   command server, and the buttons/LEDs controller.

## Tests

`tests/` contains pure-Python self-tests (no pytest hard-requirement; each
file is also runnable standalone) that pin down previously-regressed
behaviour. Notably:
- `tests/test_cava_recovery.py` -- spectrum "stuck writer" auto-recovery
- `tests/test_rotary_dispatch.py` -- rotary sampling/dispatch threading contract
- `tests/test_shutdown.py` -- power-off sequence ordering
- Many others: `test_boot.py`, `test_browse.py`, `test_hardware.py`,
  `test_leds.py`, `test_menu.py`, `test_meter.py`, `test_radio.py`, etc.

Run them with `PYTHONPATH=src python3 tests/test_NAME.py` or via pytest.

## Source note

The "official/recommended hardware" facts cited above with "(per
quadify.uk)" were cross-referenced against the project's public website,
https://quadify.uk, on **2026-08-19**. The site is treated as authoritative
for what hardware is *officially recommended*, but the actual source code
in this repo is treated as authoritative for exact pin numbers and runtime
*behavior* -- the two can and do diverge (see the HiFiBerry DAC+ note
above), and where they diverge this doc says so explicitly rather than
picking one silently. The site may also be updated independently of this
repo after this date; re-check quadify.uk directly if this note is old.

## Live-audit note (2026-08-19)

A live SSH session against the reference Pi (192.168.0.105, standard
Volumio deployment) confirmed the following, superseding any doubt left in
earlier passes of this doc:

- **Only `sable.service` runs.** `systemctl list-units` shows a single
  active unit (`sable.app --hardware --stage full` + its own `cava`
  subprocess). `sable-controls.service`, `sable-display.service`,
  `sable-ir.service` are not installed at all -- the split-process
  architecture those `.tmpl` files describe is not what's deployed. Buttons,
  LEDs, and IR all run in-process inside `sable.service` (`inputs/buttons.py`,
  `inputs/ir.py`), not via `src/sable/controls.py` (which is dead code in
  this deployment -- see `docs/ai-repair/CONFIG-FILES.md`).
- **Legacy Quadify-era units are gone**, not just disabled: `quadify.service`
  is `not-found`; `quadify-buttonsleds.service` and `ir_listener.service`
  have no unit files on disk at all (`install.sh`'s retirement loop moved
  them out during install, matching `STATUS.md`'s "CURRENT RUNTIME STATE"
  section). A stale comment in `systemd/sable.service` claiming these ran as
  "independent services" was corrected as part of this audit.
- **The Quadify-plugin `cava` dependency STATUS.md flags as still-needed is
  resolved on this Pi**: `cava` is now a plain apt package
  (`dpkg -l | grep cava` -> `cava 0.7.4-1`), and
  `/data/plugins/system_hardware/` has no `quadify` directory at all anymore
  -- only `sable`/`sableapp`. STATUS.md's "do NOT uninstall the quadify
  plugin yet" caveat looks out of date; worth the maintainer confirming and
  updating `STATUS.md` accordingly.
- **No errors in the running service's logs.** `journalctl -u sable.service`
  showed normal FSM/screensaver state transitions and exactly two transient
  `volumio: connect error: Connection refused` lines at boot (before
  Volumio's socket is up) -- not a recurring or fatal problem, the listener
  clearly reconnects since normal operation resumes immediately after.

No other dead code was found live (this was not an exhaustive static
analysis of the whole `src/` tree -- see `src/sable/controls.py` and the
`.tmpl` systemd files above as the one confirmed instance).
