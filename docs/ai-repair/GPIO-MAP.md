# Sable -- GPIO Map (BCM numbering)

All pin numbers below are **BCM** (Broadcom GPIO numbering, the numbers
`RPi.GPIO`/`gpiozero` use), not physical header pin numbers, unless stated.

Source of truth for wiring: `src/sable/hardware.py`. These are DEFAULTS --
the "standard Quadify wiring". A per-machine `config/hardware.json` can
override any of them (see `config/hardware.example.json`); absent that file,
the defaults below are what's live.

## Pins in active use, by subsystem

### OLED display (SSD1322, SPI0)

| Signal | BCM pin | Default | Source |
|---|---|---|---|
| SPI CS | CE0 | GPIO8 (via `spi_device=0`) | `src/sable/hardware.py:22` |
| RST (reset) | `rst` | **GPIO25** | `src/sable/hardware.py:23` |
| DC (data/command) | `dc` | **GPIO24** | `src/sable/hardware.py:24` |
| BLANK (forces panel dark on stop) | `blank` | **GPIO25** (same physical pin as RST) | `src/sable/hardware.py:25` |

Note: `rst` and `blank` are the SAME pin (GPIO25) by design -- see the long
comment in `src/sable/display/oled.py:52-69` explaining the dual-use
reset/blank pulse sequence needed to avoid a "blank panel on cold boot" bug.

**EVO Sabre variant** (older physical unit): DC=GPIO27, RST=GPIO24, BLANK=GPIO25
(`config/hardware.example.json:4-8`, comment in `src/sable/hardware.py:3-6`).
This override lives in `config/hardware.json` on that specific unit, not in
the default code path.

SPI bus/device: `spi_port=0`, `spi_device=0` -> `/dev/spidev0.0`
(`src/sable/hardware.py:21-22`).

### Rotary encoder

| Signal | BCM pin (default) | Source |
|---|---|---|
| CLK | **GPIO13** | `src/sable/hardware.py:34` |
| DT | **GPIO5** | `src/sable/hardware.py:35` |
| SW (push button) | **GPIO6** | `src/sable/hardware.py:36` |

This encoder is wired **directly to Pi GPIO**, separate from the MCP23017
(per source: `src/sable/inputs/rotary.py` reads `RPi.GPIO` pins directly;
confirmed by `hardware.py`'s `RotaryPins` dataclass having its own BCM pin
fields, distinct from `Mcp23017`'s I2C bus/address fields below). This
matches quadify.uk's description of the rotary module (a **KY-040**, per
quadify.uk -- not named in this repo's code) as wired with "DT, CLK, and SW
... to individual Raspberry Pi pins" (per quadify.uk/wiring.html), i.e. not
through the expander. Exact BCM numbers are code-only; quadify.uk's diagram
is an image and gives no pin numbers in extractable text.

Driver: `src/sable/inputs/rotary.py`, `RotaryEncoder.start()` (line 79) --
sets all three pins as `GPIO.IN` with `PUD_UP` (internal pull-ups), polls at
1ms intervals on a dedicated thread.

`reverse` (bool, default `True`) is a per-machine wiring flag (not a GPIO
pin) -- flips scroll direction in software if CLK/DT are physically wired
backwards. Set via `config/hardware.json` (`rotary.reverse`).

### MCP23017 I2C GPIO expander (buttons + LEDs)

Not GPIO pins directly -- an I2C-attached expander chip.

| Item | Value (default) | Source |
|---|---|---|
| I2C bus | `1` (`/dev/i2c-1`) | `src/sable/hardware.py:47` |
| I2C address | `0x20` (auto-probed 0x20-0x27 if not overridden) | `src/sable/hardware.py:48`, `src/sable/inputs/buttons.py:142-170` |
| GPIOA | 7-8 LEDs (one lit at a time) | `src/sable/hardware.py:55` |
| GPIOB | 2-column x 4-row button matrix | `src/sable/hardware.py:55` |

This means the **I2C bus pins themselves** are the only Pi GPIOs involved
here: on a Pi 4, I2C-1 is fixed to **GPIO2 (SDA)** and **GPIO3 (SCL)**
(standard Raspberry Pi I2C-1 pins -- not stated explicitly in this repo's
code since the kernel driver owns them via `dtparam=i2c_arm=on` in
`install.sh:82`; inferred from standard Pi hardware, please confirm no
override exists).

**Buttons and LEDs do NOT occupy any separate Pi GPIO pins beyond
SDA/SCL.** Confirmed in code (`src/sable/inputs/buttons.py:1-16`, `_init_mcp`
at line 172): the button matrix is read from the MCP's GPIOB register and
the LEDs are written to GPIOA, both over the I2C bus via `smbus2` -- there
is no direct `RPi.GPIO` pin claimed by buttons or LEDs anywhere in this
repo. This matches quadify.uk/mcp23017.html, which describes buttons wired
to the MCP23017's B0-B7 rows and LEDs to its A0-A7 rows, with the chip
itself reaching the Pi via only 4 wires (power, ground, SDA, SCL) -- no
direct per-button/per-LED Pi GPIO pins. Practical consequence for DAC-HAT
conflict checking: a new DAC HAT can only collide with buttons/LEDs if it
somehow also claims I2C-1 (GPIO2/3) at the same address, which would be
unusual -- see `docs/ai-repair/DAC-CHECKER.md` and the "Known DAC HAT
conflicts" section below, which now reflect this.

### IR receiver

| Signal | BCM pin (default) | Source |
|---|---|---|
| IR receiver data | **GPIO27** | `src/sable/hardware.py:112` (`IR_GPIO = 27`), set via kernel overlay `dtoverlay=gpio-ir,gpio_pin=27` (`install.sh:83`) |

Changed from GPIO4 to **GPIO27** on 2026-08-19 to match quadify.uk's
published wiring diagram (physical pin 13 = IR REMOTE = BCM27), which
disagreed with the previous code default. Updated across `hardware.py`,
`install.sh`, `install-moode.sh`, `plugin/index.js`
(`DEFAULT_IR_GPIO_PIN`), and `config/lirc/lirc_options.conf`'s comment.
**Existing installs must re-run the installer and reboot** for this to take
effect -- the overlay line in `/boot/userconfig.txt` is written at install
time, not read live from the code.

**Minor edge case (low priority):** GPIO27 is also the **EVO Sabre
variant's OLED DC override** (see the OLED section above,
`config/hardware.example.json:4-8`). EVO Sabre is the maintainer's own
one-off personal build, not a wiring most users will have -- so this only
matters if a repair agent is specifically working on that unit with IR also
enabled. Not a conflict on standard (non-EVO) wiring, where DC defaults to
GPIO24 and GPIO27 is otherwise unused.

The IR pin is **kernel-overlay-owned**, not read directly by Sable's Python
-- Sable's `inputs/ir.py` reads decoded key events from the LIRC daemon's
unix socket (`/run/lirc/lircd`), not the GPIO directly. Changing the pin
requires editing `/boot/userconfig.txt`'s `dtoverlay=gpio-ir,gpio_pin=N`
line -- there is a helper script for this: `bin/sable-set-ir-pin.sh`.

### Hardware power/shutdown button

| Signal | BCM pin (default) | Source |
|---|---|---|
| Power button | **GPIO17** | `src/sable/hardware.py:113` (`POWER_BUTTON_GPIO = 17`), kernel overlay `dtoverlay=gpio-shutdown,gpio_pin=17,active_low=1,gpio_pull=up` (`install.sh:84`) |

Explicitly commented as **"handled by gpio-shutdown overlay, never in
software"** (`src/sable/hardware.py:113`) -- this is a kernel-level safe
shutdown trigger, entirely independent of the Sable Python app. Note this is
DIFFERENT from the MCP23017 button-8 "hold to power off" software path
(`src/sable/inputs/buttons.py`, `_HOLD_BUTTONS = {8}`), which is a separate,
software-driven power-off sequence over I2C, not this GPIO17 line.

## Summary table -- all Pi GPIO pins referenced in code

| BCM pin | Function | Overridable? |
|---|---|---|
| GPIO2 | I2C1 SDA (MCP23017 bus) | fixed by Pi I2C-1 hardware (inferred, please confirm) |
| GPIO3 | I2C1 SCL (MCP23017 bus) | fixed by Pi I2C-1 hardware (inferred, please confirm) |
| GPIO5 | Rotary DT | yes, via `config/hardware.json` `rotary.dt` |
| GPIO6 | Rotary SW (push) | yes, via `config/hardware.json` `rotary.sw` |
| GPIO8 | OLED SPI CS (CE0) | yes, via `spi_device` (rarely changed) |
| GPIO9/10/11 | SPI0 MISO/MOSI/SCLK | fixed by SPI0 hardware, not configurable in this repo |
| GPIO13 | Rotary CLK | yes, via `config/hardware.json` `rotary.clk` |
| GPIO17 | Hardware power/shutdown button (kernel overlay) | yes, edit `install.sh`'s overlay line directly |
| GPIO24 | OLED DC (standard wiring) / OLED RST (EVO Sabre wiring) | yes, via `config/hardware.json` `oled.dc` / `oled.rst` |
| GPIO25 | OLED RST + BLANK (standard wiring) / OLED BLANK (EVO Sabre wiring) | yes, via `config/hardware.json` `oled.rst` / `oled.blank` |
| GPIO27 | IR receiver data (default, changed 2026-08-19) / OLED DC (EVO Sabre wiring only -- maintainer's own one-off build, unlikely to affect most users, see IR receiver section above) | yes, via `config/settings.json` `ir` section + `bin/sable-set-ir-pin.sh` (rewrites `userconfig.txt`) |

## Known DAC HAT conflicts

**For a repeatable diagnostic procedure covering ANY DAC HAT (not just
HiFiBerry), see `docs/ai-repair/DAC-CHECKER.md`** -- it walks an AI repair
agent through identifying the active overlay, looking up its pins, and
remapping this project's own GPIOs if they collide.

quadify.uk names the **HiFiBerry DAC+** as the officially recommended DAC
HAT for this project. Per the maintainer (2026-08-19): it's recommended
specifically because it does **not** use the I2C bus (SDA/GPIO2, SCL/GPIO3),
so it never contends with the MCP23017 expander that drives Sable's buttons
and LEDs -- not because of I2S pin compatibility in general (I2S pin usage
is largely standardized across DAC HATs anyway; the I2C bus is the pin
class that actually varies and actually matters here, since some non-DAC
add-on boards and a few DAC HAT variants with onboard EEPROMs or codecs do
sit on I2C). Nothing in this repo's code installs, configures, or
specifically checks for a HiFiBerry overlay (DAC choice is left entirely to
Volumio/moOde -- see `docs/ai-repair/OVERVIEW.md`'s "Source note" section
for the full discrepancy). The analysis below is this repo's own
independent pin-conflict check, not something quadify.uk states directly.

**Only two things ever need checking against a new DAC HAT's pins: the
rotary encoder's direct GPIO pins, and (rarely) the I2C bus pins
(GPIO2/GPIO3) if a DAC HAT were ever to also claim I2C-1.** Buttons and
LEDs are NOT a separate concern here -- they live entirely behind the
MCP23017 on the same I2C bus (see the MCP23017 section above), so a DAC
HAT's GPIO usage cannot collide with buttons/LEDs individually, only with
the I2C bus as a whole (which no known DAC overlay does).

**General knowledge (not verified against this repo's code -- stated here
because it is common, well-known information about HiFiBerry HATs, useful
for diagnosing conflicts with the pins above):**

HiFiBerry DAC+ / DAC2 HD and most I2S-based DAC HATs use the Raspberry Pi's
I2S audio interface, which occupies:
- **GPIO18** (BCLK)
- **GPIO19** (LRCLK / word select)
- **GPIO20** (DIN)
- **GPIO21** (DOUT)

Some HiFiBerry card variants additionally use **GPIO4** or another GPIO for
a hardware mute/unmute line, depending on the specific card revision and
overlay used (`dtoverlay=hifiberry-dacplus` etc. define this per-card in the
Raspberry Pi kernel overlay source, not in this repo).

### Cross-reference against Sable's actual GPIO usage

| DAC HAT pin (general HiFiBerry knowledge) | Overlaps a Sable pin? |
|---|---|
| GPIO18 (I2S BCLK) | No conflict found in this repo's default pinout. |
| GPIO19 (I2S LRCLK) | No conflict found in this repo's default pinout. |
| GPIO20 (I2S DIN) | No conflict found in this repo's default pinout. |
| GPIO21 (I2S DOUT) | No conflict found in this repo's default pinout. |
| GPIO4 (some cards' hardware-mute line) | **No conflict as of 2026-08-19** -- Sable's IR receiver pin was moved off GPIO4 to GPIO27 (see the IR receiver section above) specifically because it matches quadify.uk's published wiring diagram; GPIO4 is now unused by Sable's default pinout. If diagnosing an older install that hasn't been re-installed/rebooted since this change, it may still be on GPIO4 -- check with `raspi-gpio` or `cat /boot/userconfig.txt` (or `/boot/firmware/config.txt` on Bookworm) rather than assuming the current default. |
| GPIO27 (rare -- some HATs/add-ons route an extra control line here) | This is now Sable's default IR pin as of 2026-08-19. Also the EVO Sabre wiring's OLED DC override, but that's the maintainer's own personal build, not something most users have -- low-priority edge case. |

No other repo-verified conflicts were found; the OLED (SPI0 + GPIO24/25),
rotary (GPIO5/6/13), and MCP23017 (I2C GPIO2/3) pins do not overlap the
general HiFiBerry I2S pin set above.

## Other DAC HAT profiles

(Template only -- no other DAC HAT has been investigated in this repo. Add
entries here as they are confirmed, following the same structure.)

### `<DAC HAT name>`

- **Pins used:**
- **Conflicts with this repo:**
- **Notes:**
