"""Slice: LED expression logic (no hardware -- the MCP bus is only opened in
start(), which we never call). Run: python3 tests/test_leds.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sable.inputs.buttons import (ButtonsLeds, LED_PLAY, LED_PAUSE, LED_SWEEP)
from sable.state import StateStore
from sable import hardware


def _bl(status):
    store = StateStore(log=lambda *a: None)
    store.apply_pushstate({"status": status})
    return ButtonsLeds(hardware.MCP, lambda *a: None, store, log=lambda *a: None)


def test_play_is_solid():
    assert _bl("play")._desired_led() == LED_PLAY


def test_stopped_is_dark():
    assert _bl("stop")._desired_led() == 0


def test_paused_pulses():
    bl = _bl("pause")
    assert bl._desired_led(now=0.0) == LED_PAUSE     # on phase of the heartbeat
    assert bl._desired_led(now=1.2) == 0             # off phase


def test_boot_sweep_covers_all_seven_leds():
    bits = 0
    for led in LED_SWEEP:
        bits |= led
    assert bits == 0b1111111                          # all 7 LED bits set
    assert len(LED_SWEEP) == 7
    assert len(set(LED_SWEEP)) == 7                   # no duplicates


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print("\nAll %d LED tests passed." % len(tests))


if __name__ == "__main__":
    main()
