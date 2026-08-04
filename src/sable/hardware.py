"""Sable hardware contract.

DEFAULTS are the standard Quadify wiring (RPi4; the official pinout -- OLED
DC=GPIO24, RST=GPIO25, which are also luma's defaults). A board wired differently
-- notably the original EVO Sabre unit (OLED DC=27, RST=24) -- overrides only the
pins it changes via config/hardware.json (see config/hardware.example.json).

These are NOT user settings: they describe physical wiring and are never exposed as
editable config. User preferences live in settings.py instead -- a deliberate break
from the old config.yaml, which mixed immutable pin numbers with user prefs and
drifted across four stores.
"""
import dataclasses
import json
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class OledPins:
    spi_port: int = 0
    spi_device: int = 0          # /dev/spidev0.0 (CS on CE0 / GPIO8)
    rst: int = 25                # BCM, luma drives this (standard pinout; EVO=24)
    dc: int = 24                 # BCM (standard pinout; EVO unit overrides to 27)
    blank: int = 25              # BCM, pulled low ~1s on stop to force panel dark
    width: int = 256
    height: int = 64
    # NB: panel rotation is NOT here -- it's a USER setting (display.rotate, in
    # DEGREES) in settings.py, editable from the web UI. Wiring only below.


@dataclass(frozen=True)
class RotaryPins:
    clk: int = 13                # BCM
    dt: int = 5                  # BCM
    sw: int = 6                  # BCM
    long_press_s: float = 2.5
    reverse: bool = True         # True if CLK/DT are wired so that clockwise
                                 # decodes as "down". Flips scroll direction for
                                 # BOTH volume and menu (one source). Per-MACHINE
                                 # wiring, set in config/hardware.json -- like
                                 # Mcp23017.led_reverse.


@dataclass(frozen=True)
class Mcp23017:
    bus: int = 1                 # /dev/i2c-1
    addr: int = 0x20
    IODIRA: int = 0x00
    IODIRB: int = 0x01
    GPPUB: int = 0x0D
    GPIOA: int = 0x12
    GPIOB: int = 0x13
    OLATA: int = 0x14
    # GPIOA = 7 LEDs (one lit at a time); GPIOB = 2col x 4row button matrix
    col_settle_s: float = 0.005
    swap_columns: bool = True
    led_reverse: bool = True     # True ONLY if the LED ribbon is plugged in
                                 # reversed (LED order flipped end-to-end, so
                                 # play/pause show at the BOTTOM). Per-MACHINE
                                 # wiring, set in config/hardware.json. See
                                 # led_byte() below. Buttons (GPIOB) unaffected.


@dataclass(frozen=True)
class Contrast:
    low: int = 50
    medium: int = 150
    high: int = 255


# Per-MACHINE wiring overrides. The dataclasses above are the DEFAULTS (standard
# Quadify wiring). A board wired differently drops a config/hardware.json next to
# settings.json with only the pins it changes -- e.g. the original EVO Sabre unit:
#   {"oled": {"dc": 27, "rst": 24}}
# This is NOT a user setting (never exposed in the menu/plugin) -- it describes
# physical wiring. Absent file or keys -> the defaults below. gitignored, like
# settings.json (one per machine); see config/hardware.example.json.
_OVERRIDE_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "config", "hardware.json"))


def _load_overrides():
    try:
        with open(_OVERRIDE_PATH, "r") as fh:
            return json.load(fh) or {}
    except (FileNotFoundError, ValueError, OSError):
        return {}


def _build(cls, key, overrides):
    """A frozen `cls` instance with any known keys from overrides[key] applied."""
    valid = {f.name for f in dataclasses.fields(cls)}
    changes = {k: v for k, v in (overrides.get(key) or {}).items() if k in valid}
    return dataclasses.replace(cls(), **changes) if changes else cls()


_OVERRIDES = _load_overrides()
CONTRAST = Contrast()
OLED = _build(OledPins, "oled", _OVERRIDES)
ROTARY = _build(RotaryPins, "rotary", _OVERRIDES)
MCP = _build(Mcp23017, "mcp", _OVERRIDES)

# Kernel overlays this project owns (set by install, not at runtime).
# /boot/userconfig.txt:
#   dtparam=spi=on
#   dtparam=i2c_arm=on
#   dtoverlay=gpio-ir,gpio_pin=4
#   dtoverlay=gpio-shutdown,gpio_pin=17,active_low=1,gpio_pull=up
# Audio output (USB DAC / I2S HAT / HDMI) is Volumio's concern -- Sable adds no
# audio overlay here.
IR_GPIO = 4
POWER_BUTTON_GPIO = 17           # handled by gpio-shutdown overlay, never in software

# LED bit positions on GPIOA (one lit at a time).
LED_PLAY = 0
LED_PAUSE = 1
LED_PREV = 2
LED_NEXT = 3
LED_SHUFFLE = 4
LED_REPEAT = 5
LED_SPARE = 6
LED_SPARE2 = 7


def led_byte(mask, mcp=None):
    """Translate a logical LED bitmask into the byte to WRITE to GPIOA.

    Standard wiring -> identity. On a unit whose LED ribbon is plugged in
    reversed (mcp.led_reverse), the 8 LED lines run end-to-end backwards, so
    logical bit b physically lands on LED (8-b) -- play/pause appear at the
    bottom. Pre-reversing the byte across all 8 bits cancels that: play (bit 0)
    goes out as bit 7 and lands back on LED 1 (and bit 7 back to LED 8). This is
    the LED analogue of the button matrix's swap_columns.
    """
    mcp = MCP if mcp is None else mcp
    if not getattr(mcp, "led_reverse", False):
        return mask
    out = 0
    for b in range(8):
        if mask & (1 << b):
            out |= 1 << (7 - b)
    return out

# DAC input labels, for DACs whose own remote cycles a hardware input selector
# (e.g. the Audiophonics EVO Sabre). The Pi cannot read or set the input; this is a
# display HINT only. Order MUST match the order the DAC cycles on each INPUT press.
DAC_INPUTS = ("USB", "RPI", "COAX", "OPT", "BT")
