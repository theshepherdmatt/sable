"""Sable hardware contract -- FROZEN for this board (RPi4 + EVO Sabre in a Quad case).

These are NOT user settings. They describe physical wiring and must never be
exposed as editable config. User preferences live in settings.py instead.
Splitting the two is a deliberate break from the old config.yaml, which mixed
immutable pin numbers with user prefs and drifted across four stores.
"""
import dataclasses
import json
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class OledPins:
    spi_port: int = 0
    spi_device: int = 0          # /dev/spidev0.0
    rst: int = 24                # BCM, luma drives this at runtime
    dc: int = 27                 # BCM
    blank: int = 25              # BCM, pulled low ~1s on stop to force panel dark
    width: int = 256
    height: int = 64
    rotate: int = 0              # luma rotate (x90). This unit (Evo panel in a
                                 # Quad case) reads right-side-up at 0 -- CONFIRMED
                                 # on the real OLED 2026-06-10. (config.yaml's
                                 # conflicting rotate:180 was a desynced stray.)


@dataclass(frozen=True)
class RotaryPins:
    clk: int = 13                # BCM
    dt: int = 5                  # BCM
    sw: int = 6                  # BCM
    long_press_s: float = 2.5


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


@dataclass(frozen=True)
class Contrast:
    low: int = 50
    medium: int = 150
    high: int = 255


# Per-MACHINE wiring overrides. The dataclasses above are the frozen DEFAULTS (the
# original EVO Sabre unit). A board wired differently drops a config/hardware.json
# next to settings.json with only the pins it changes, e.g.:
#   {"oled": {"dc": 24, "rst": 25, "blank": 25}}
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
# The EVO Sabre DAC is USB-XMOS audio-class; NO I2S/HAT overlay belongs here.
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

# DAC input labels mirrored from the EVO Sabre remote selector.
# The Pi cannot read or set the DAC input; the remote is the only control.
# Order MUST match the order the DAC cycles through on each INPUT press.
DAC_INPUTS = ("USB", "RPI", "COAX", "OPT", "BT")
