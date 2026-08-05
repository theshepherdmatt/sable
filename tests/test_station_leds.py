"""Self-tests for the station LED policy.

On a radio layout the lit LED should be the button whose station is playing --
an FM4 lights the button you pressed. But a panel configured as ordinary
transport must keep the old play/pause behaviour untouched, which is the part
most at risk of being broken by this.

Run: pytest tests/test_station_leds.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sable import stations
from sable.inputs.buttons import (LED_PAUSE, LED_PLAY, LED_PREV, ButtonsLeds)
from sable.state import PlayerState


class _Store:
    def __init__(self, **kw):
        self.state = PlayerState(**kw)

    def get(self):
        return self.state


class _Settings:
    def __init__(self, buttons):
        self.buttons = buttons

    def get(self, *path, default=None):
        if path[:1] == ("buttons",) and len(path) == 2:
            return self.buttons.get(path[1], default)
        return default


def _btns(buttons, **state):
    b = ButtonsLeds.__new__(ButtonsLeds)
    b.store = _Store(**state)
    b._app = type("A", (), {"settings": _Settings(buttons),
                            "asleep": False, "_pause_idle": False})()
    return b


_TRANSPORT = {
    "btn_1": {"action": "play", "arg": ""},
    "btn_2": {"action": "pause", "arg": ""},
}
_FM4 = {
    "btn_1": {"action": "bbc_radio_1", "arg": ""},
    "btn_3": {"action": "bbc_radio_2", "arg": ""},
}
_R2_URI = stations.PRESETS["bbc_radio_2"][1]


def test_station_button_led_lights_instead_of_the_play_led():
    """BBC 2 on button 3 -> LED 3, not LED 1."""
    b = _btns(_FM4, status="play", uri=_R2_URI, service="webradio")
    assert b._desired_led() == LED_PREV      # bit 2 == button 3's LED
    assert b._desired_led() != LED_PLAY


def test_transport_layout_is_completely_unaffected():
    """The explicit requirement: a panel with the usual play/pause buttons must
    behave exactly as it did before this feature existed."""
    b = _btns(_TRANSPORT, status="play", uri=_R2_URI, service="mpd")
    assert b._desired_led() == LED_PLAY
    b = _btns(_TRANSPORT, status="pause", uri=_R2_URI, service="mpd")
    assert b._desired_led(now=0.0) == LED_PAUSE


def test_playing_something_that_is_not_on_any_button_shows_play():
    b = _btns(_FM4, status="play", uri="mpd:/music/song.flac", service="mpd")
    assert b._desired_led() == LED_PLAY


def test_stopped_stays_dark_even_on_a_station_button():
    b = _btns(_FM4, status="stop", uri=_R2_URI, service="webradio")
    assert b._desired_led() == 0


def test_a_hand_entered_stream_lights_its_button_too():
    """ILR buttons carry a pasted URL rather than a preset key -- same rule."""
    uri = "http://example.com/ilr.m3u8"
    buttons = {"btn_3": {"action": "play_uri",
                         "arg": "%s | Capital | logo.png" % uri}}
    b = _btns(buttons, status="play", uri=uri, service="webradio")
    assert b._desired_led() == LED_PREV


def test_match_is_on_uri_not_title():
    """Volumio rewrites a stream's title to the current programme, so matching
    on title lights the button for a second and then drops it."""
    b = _btns(_FM4, status="play", uri=_R2_URI, service="webradio",
              title="Sounds of the 80s with Gary Davies")
    assert b._desired_led() == LED_PREV
