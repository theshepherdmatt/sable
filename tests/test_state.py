"""Phase 1 self-tests: state model, volume folding, status fan-out, backoff.

Pure logic, no socket -- proves the state transitions and the LED-fanout trigger
without changing any live playback. Run: python3 tests/test_state.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sable.state import PlayerState, StateStore
from sable.volumio.listener import backoff_delay
from sable import controls


def _silent():
    return StateStore(log=lambda *a: None)


def test_blank_metadata_mid_stream_keeps_last_track():
    """AirPlay pause pushes status=play with EMPTY title/artist and the default
    albumart -- keep the last-known track rather than blanking to (no title)+logo."""
    s = _silent()
    s.apply_pushstate({"status": "play", "title": "Bang Bien", "artist": "NoW",
                       "albumart": "/albumart?web=x", "service": "airplay_emulation"})
    s.apply_pushstate({"status": "play", "title": "", "artist": "",
                       "albumart": "/albumart"})        # AirPlay "pause" blank
    st = s.get()
    assert st.title == "Bang Bien"                       # kept
    assert st.artist == "NoW"
    assert st.albumart == "/albumart?web=x"
    # a real new track (non-empty title) still replaces
    s.apply_pushstate({"title": "New Song", "artist": "B"})
    assert s.get().title == "New Song" and s.get().artist == "B"


def test_pushstate_play_then_partial_pause():
    s = _silent()
    s.apply_pushstate({"status": "play", "title": "Song", "artist": "A",
                       "volume": 40, "service": "mpd"})
    assert s.get().status == "play"
    assert s.get().title == "Song"
    assert s.get().volume == 40
    # partial push (pause only) must keep the title
    s.apply_pushstate({"status": "pause"})
    assert s.get().status == "pause"
    assert s.get().title == "Song"


def test_volume_event_forms():
    s = _silent()
    s.apply_volume(55)
    assert s.get().volume == 55
    s.apply_volume({"vol": 12, "mute": True})
    assert s.get().volume == 12
    assert s.get().mute is True


def test_merged_keeps_previous_on_null():
    st = PlayerState(title="X", volume=9)
    m = st.merged({"status": "play", "title": None})
    assert m.title == "X" and m.volume == 9 and m.status == "play"


def test_fanout_only_on_status_change():
    s = _silent()
    sent = []
    s.subscribe(lambda o, n: sent.append(n.status) if o.status != n.status else None)
    s.apply_pushstate({"status": "play", "volume": 1})
    s.apply_pushstate({"status": "play", "volume": 2})   # volume only -> no LED change
    s.apply_pushstate({"status": "stop"})
    assert sent == ["play", "stop"], sent


def test_status_led_mask():
    assert controls.status_led_mask("play") == 1 << 0
    assert controls.status_led_mask("pause") == 1 << 1
    assert controls.status_led_mask("stop") == 1 << 1
    assert controls.status_led_mask("unknown") == 0


def test_backoff_sequence():
    assert backoff_delay(1) == 2
    assert backoff_delay(5) == 10
    assert backoff_delay(100) == 60


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print("\nAll %d state tests passed." % len(tests))


if __name__ == "__main__":
    main()
