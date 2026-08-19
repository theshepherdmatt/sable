# Sable -- DAC HAT Conflict Checker (procedure)

A repeatable procedure for an AI repair agent to diagnose "rotary encoder
(or IR) stopped working after installing/swapping a DAC HAT" on **whatever
DAC HAT the user actually has** -- not just HiFiBerry (quadify.uk names
HiFiBerry DAC+ as the officially recommended card, but this repo's code
does not assume or install any specific DAC). This is a PROCEDURE, not a
pinout database: steps 1-2 below tell you how to find the facts for the
user's specific board rather than assuming them.

Note: buttons and LEDs are very unlikely to be affected by a DAC HAT swap
at all -- they live entirely behind the MCP23017 I2C expander, not on
direct Pi GPIO pins (see Step 3 below and `docs/ai-repair/GPIO-MAP.md`). If
buttons/LEDs stopped working after a DAC swap, suspect a broken I2C bus
(`i2cdetect -y 1`) rather than a GPIO pin conflict.

Related docs: `docs/ai-repair/GPIO-MAP.md` (this project's own pin usage,
plus the one DAC conflict already documented there), `docs/ai-repair/
COMMON-FIXES.md` (the "Rotary encoder not responding after a DAC HAT swap"
entry, which points here), `docs/ai-repair/SAFE-EDITING-RULES.md`
(guardrails -- read before making any change).

---

## Step 1 -- Determine the active DAC overlay on the Pi

Find out which DAC overlay is actually loaded. This lives in the Pi's boot
config, **outside this repo**:

```
grep -i '^dtoverlay=' /boot/config.txt 2>/dev/null
grep -i '^dtoverlay=' /boot/firmware/config.txt 2>/dev/null
```

Check both paths -- on Bookworm-based images the live file is often
`/boot/firmware/config.txt`, with `/boot/config.txt` sometimes present as an
inert stub that silently swallows writes. `README.md:228` (in this repo)
flags exactly this gotcha for moOde; treat it as a general Bookworm risk,
not moOde-specific.

Look for a line naming a DAC overlay, e.g. (this list is illustrative --
match whatever is actually present, don't assume one of these):
```
dtoverlay=hifiberry-dac
dtoverlay=hifiberry-dacplus
dtoverlay=hifiberry-dacplusadc
dtoverlay=hifiberry-digi
dtoverlay=allo-boss-dac
dtoverlay=iqaudio-dac
dtoverlay=audioinjector-wm8731-audio
```
(or any other `dtoverlay=` line that looks like a sound-card overlay --
names vary by vendor).

**Does this repo touch `/boot/config.txt` or `/boot/firmware/config.txt`
itself?** No. Checked `install.sh` and `plugin/install.sh`: both only write
to **`/boot/userconfig.txt`** (`install.sh:23,73-84`, function `add_overlay`,
adding Sable's own `dtparam=spi=on` / `dtparam=i2c_arm=on` /
`dtoverlay=gpio-ir,...` / `dtoverlay=gpio-shutdown,...` lines), and
`bin/sable-set-ir-pin.sh` also only edits `/boot/userconfig.txt:9`. Neither
script reads, writes, or otherwise references `config.txt` or
`firmware/config.txt` anywhere in this repo. `userconfig.txt` is a Raspberry
Pi OS mechanism that gets appended into the real `config.txt` at boot time
by the firmware itself -- so the DAC overlay (added by Volumio/moOde's own
setup, not by Sable) and Sable's overlays end up combined at boot, but they
are configured in different files by different installers. **Do not
conflate the two, and do not add DAC-related lines to `userconfig.txt` --
that file belongs to Sable's own overlays only** (see Step 4 and
SAFE-EDITING-RULES.md).

## Step 2 -- Cross-reference the overlay against known pin usage

**(a) Check this repo's own documentation first.** Open
`docs/ai-repair/GPIO-MAP.md` and look under "Known DAC HAT conflicts" and
"Other DAC HAT profiles":
- If the overlay you found in Step 1 matches the HiFiBerry entry already
  there, you have your pin list (GPIO18/19/20/21 I2S, possibly a per-card
  mute pin -- see GPIO-MAP.md for the current state of that check) and can
  skip to Step 3.
- If a future session has since added a matching entry under "Other DAC HAT
  profiles" for this exact board, use that instead.
- If neither matches the user's overlay, continue to (b).

**(b) Look up the specific board's pin usage externally.** This repo
cannot tell you the pin usage of a DAC HAT it has no code for. You need to
find, from a source outside this repo:
- The vendor's own documentation/datasheet for the specific board, or
- The Raspberry Pi kernel's overlay source/docs for that overlay name (the
  overlay `.dts`/`.dtbo` source or the kernel's `overlays/README`), which
  lists exactly which GPIOs a given `dtoverlay=` claims.

**Treat whatever you find here as EXTERNAL information requiring
verification with the user or a live check on the Pi** -- it is not sourced
from this repo and must not be presented as if it were. As general
background (not verified in this repo, standard knowledge only): most I2S
DAC HATs use GPIO18 (BCLK), GPIO19 (LRCLK), GPIO20 (DIN), GPIO21 (DOUT) as
the common baseline, but many boards ALSO claim one or more extra GPIOs for
things like mute, reset, or amplifier-enable lines, and those extras vary
board to board -- there is no universal answer for them. Do not guess a
number; either find it documented for that exact board, or verify it on the
live hardware (Step 2c).

**(c) If you cannot find documented pin numbers, verify empirically
instead of guessing.** On the Pi, with the DAC overlay active:
```
raspi-gpio get
```
(or `gpioinfo` if `raspi-gpio` is unavailable) shows which GPIO lines are
currently claimed/driven and by which function. Compare the claimed lines
against this project's own pins (Step 3). This sidesteps needing the vendor
datasheet at all, at the cost of needing the hardware live in front of you
(or the user willing to run one command over SSH and paste the output back).

## Step 3 -- Compare occupied DAC pins against this project's pins

Once you have the DAC HAT's occupied GPIOs (from 2a, 2b, or 2c), compare
them against Sable's own GPIO usage, fully documented in
`docs/ai-repair/GPIO-MAP.md` ("Summary table -- all Pi GPIO pins referenced
in code"). In short, the pins this project claims by default are:

| Subsystem | BCM pins (default) |
|---|---|
| Rotary encoder | GPIO13 (CLK), GPIO5 (DT), GPIO6 (SW) |
| OLED | GPIO24 (DC), GPIO25 (RST/BLANK), GPIO8 (CS/CE0), GPIO9/10/11 (SPI0) |
| MCP23017 (buttons+LEDs) | GPIO2 (SDA), GPIO3 (SCL) -- I2C-1 |
| IR receiver | GPIO27 (changed from GPIO4 on 2026-08-19 -- see GPIO-MAP.md) |
| Hardware power button (kernel overlay, not Sable software) | GPIO17 |

Any overlap between the DAC HAT's occupied pins and this table is the
conflict. GPIO27 (IR) is also the EVO Sabre wiring variant's OLED DC pin,
but EVO Sabre is the maintainer's own personal build -- rare, only worth
checking if you know the unit in front of you is one. GPIO4 is no longer
used by this project's default pinout (freed up when IR moved to GPIO27),
but an older, not-yet-reinstalled/rebooted unit may still have IR on GPIO4
-- verify with `raspi-gpio get` or by reading `/boot/userconfig.txt` rather
than assuming either default. Rotary pins
GPIO5/6/13 and the OLED pins GPIO8/24/25 are the other candidates worth
checking against a given board, since header-adjacent HATs sometimes claim
more of the 40-pin header than just the four core I2S lines.

**In practice, the only subsystems worth actively checking are the rotary
encoder's direct GPIO pins (GPIO5/6/13) and, rarely, the I2C-1 bus pins
(GPIO2/3).** Buttons and LEDs are confirmed in code
(`src/sable/inputs/buttons.py`) to live entirely behind the MCP23017 over
I2C -- there is no separate Pi GPIO pin per button or per LED, so a DAC
HAT's own GPIO usage cannot conflict with buttons/LEDs individually. The
only way a DAC HAT affects buttons/LEDs at all is if it somehow also claims
the I2C-1 bus itself (GPIO2/GPIO3) -- rare, and would break the MCP23017
bus entirely rather than causing a subtle per-button glitch. If you're
diagnosing "buttons/LEDs stopped working after a DAC swap," look first at
whether the DAC install broke I2C generally (e.g. `i2cdetect -y 1` shows
nothing) rather than assuming a GPIO pin conflict.

## Step 4 -- If there's a conflict, remap THIS project's pins

Never move the DAC HAT's pins (its overlay is fixed by its own hardware
design) and never edit `/boot/config.txt`/`/boot/firmware/config.txt`
directly for this purpose -- that's Volumio/moOde/firmware territory, out
of scope per `docs/ai-repair/SAFE-EDITING-RULES.md`. Instead, move
**Sable's own** pins, which is exactly what the per-machine override
mechanism exists for:

- **Rotary encoder pins** (CLK/DT/SW) and **OLED pins** (DC/RST/BLANK): the
  DEFAULTS are defined in `src/sable/hardware.py` (`RotaryPins` dataclass,
  lines 33-42; `OledPins` dataclass, lines 20-29). **Do not edit
  `hardware.py` itself.** Instead create or edit
  **`config/hardware.json`** on the Pi (template + explanation at
  `config/hardware.example.json`) with only the keys that need to change,
  e.g.:
  ```json
  {
    "rotary": { "clk": 26, "dt": 16, "sw": 12 }
  }
  ```
  Pick replacement GPIO pins that are (a) free per the `raspi-gpio get` /
  vendor-doc check in Step 2, and (b) not already used elsewhere in the
  table in Step 3.
- **IR receiver pin** (GPIO27 by default as of 2026-08-19, previously
  GPIO4): this is kernel-overlay-owned, not
  a `hardware.json` key. Use `bin/sable-set-ir-pin.sh <new-bcm-pin>` (run
  as root/with sudo), which rewrites the `dtoverlay=gpio-ir,gpio_pin=`
  line in `/boot/userconfig.txt:9` -- this is the one Sable-owned line in
  that file, and the script is the intended, safe way to change it (see
  `docs/ai-repair/CONFIG-FILES.md`).
- **MCP23017 (buttons+LEDs) I2C address**, if for some reason the DAC HAT
  also uses I2C-1 at the same address (rare, but the `hardware.json`
  override exists): `mcp.addr` under `config/hardware.json`, corresponding
  to `Mcp23017.addr` in `src/sable/hardware.py:48`. The I2C **bus pins**
  themselves (GPIO2/GPIO3) cannot be moved without different Pi hardware --
  if a DAC HAT genuinely occupies GPIO2/3, that HAT is not compatible with
  this project's I2C-based buttons/LEDs at all, and that should be
  surfaced to the user rather than "fixed."

After editing `config/hardware.json`, optionally record what board/overlay
this machine has and which pins were moved, as a comment or as a new entry
under "Other DAC HAT profiles" in `docs/ai-repair/GPIO-MAP.md`, so a future
repair session (or a different Sable install with the same DAC HAT) doesn't
have to redo this lookup from scratch. Only add pin numbers you actually
confirmed in Step 2 -- do not backfill a guess.

## Step 5 -- Guardrails reminder

This procedure only ever touches:
- `config/hardware.json` (this project's own per-machine wiring override), and
- the one Sable-owned `gpio-ir` line in `/boot/userconfig.txt`, via
  `bin/sable-set-ir-pin.sh`.

It never touches `/boot/config.txt`, `/boot/firmware/config.txt`, the DAC
HAT's own overlay line, Volumio/moOde core, or anything else outside this
project, per `docs/ai-repair/SAFE-EDITING-RULES.md`.

As with every change in this project: **after editing `config/hardware.json`
or running `bin/sable-set-ir-pin.sh`, do not restart any service yourself.**
Tell the user the change is made and ask them to **reboot the Pi** to apply
it.
