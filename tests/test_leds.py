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


def test_paused_pulses_when_active():
    bl = _bl("pause")                                # no app -> active pause
    assert bl._desired_led(now=0.0) == LED_PAUSE     # on phase of the heartbeat
    assert bl._desired_led(now=1.2) == 0             # off phase


class _FakeApp:
    asleep = False
    _pause_idle = False


def test_paused_solid_when_idle_on_clock():
    bl = _bl("pause")
    bl._app = _FakeApp()
    bl._app._pause_idle = True
    assert bl._desired_led(now=0.0) == LED_PAUSE     # solid -- both phases lit
    assert bl._desired_led(now=1.2) == LED_PAUSE


def test_paused_solid_when_asleep():
    bl = _bl("pause")
    bl._app = _FakeApp()
    bl._app.asleep = True
    assert bl._desired_led(now=0.0) == LED_PAUSE
    assert bl._desired_led(now=1.2) == LED_PAUSE


def test_airplay_shows_no_status_led():
    # Volumio can't tell play from pause for AirPlay, so no status LED at all.
    bl = _bl("play")
    bl.store.apply_pushstate({"service": "airplay_emulation"})
    assert bl._desired_led() == 0
    bl.store.apply_pushstate({"status": "pause"})    # still AirPlay -> still none
    assert bl._desired_led() == 0


def test_led_byte_identity_by_default():
    # Standard wiring: the byte written to GPIOA is the logical mask, unchanged.
    assert hardware.led_byte(LED_PLAY) == LED_PLAY
    assert hardware.led_byte(LED_PAUSE) == LED_PAUSE
    assert hardware.led_byte(0) == 0


def test_led_byte_reversed_ribbon():
    # Reversed LED ribbon: the byte is flipped end-to-end so play/pause, which
    # would otherwise land on the bottom LEDs, are written to the top.
    import dataclasses
    rev = dataclasses.replace(hardware.MCP, led_reverse=True)
    assert hardware.led_byte(LED_PLAY, rev) == 1 << 7    # play  -> far bit
    assert hardware.led_byte(LED_PAUSE, rev) == 1 << 6   # pause -> next bit
    assert hardware.led_byte(0, rev) == 0
    # Round-trip: reversing twice is the identity for any single LED.
    for led in LED_SWEEP:
        assert hardware.led_byte(hardware.led_byte(led, rev), rev) == led


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
