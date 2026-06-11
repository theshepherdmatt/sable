"""Hardware pin override loader (config/hardware.json). Run: python3 tests/test_hardware.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sable.hardware import OledPins, RotaryPins, _build


def test_no_override_uses_defaults():
    out = _build(OledPins, "oled", {})
    assert out.dc == OledPins().dc and out.rst == OledPins().rst


def test_override_applies_known_keys_only():
    out = _build(OledPins, "oled", {"oled": {"dc": 24, "rst": 25}})
    assert out.dc == 24 and out.rst == 25
    assert out.blank == OledPins().blank          # untouched key keeps default
    assert out.spi_device == OledPins().spi_device


def test_unknown_keys_ignored():
    out = _build(OledPins, "oled", {"oled": {"bogus": 99, "dc": 24}})
    assert out.dc == 24
    assert not hasattr(out, "bogus")


def test_other_sections_independent():
    ov = {"oled": {"dc": 24}, "rotary": {"clk": 99}}
    assert _build(OledPins, "oled", ov).dc == 24
    assert _build(RotaryPins, "rotary", ov).clk == 99
    assert _build(RotaryPins, "rotary", {}).clk == RotaryPins().clk


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print("\nAll %d hardware tests passed." % len(tests))


if __name__ == "__main__":
    main()
