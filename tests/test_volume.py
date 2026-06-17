"""Rotary/IR volume control. Run: python3 tests/test_volume.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sable.app import build_sim_app


class FakeVol:
    def __init__(self):
        self.calls = []
        self.browse_sources = []

    def set_volume(self, arg):
        self.calls.append(arg)

    def get_sources(self):
        pass


def test_nudge_emits_direction_and_flags_osd():
    app = build_sim_app(frames_dir=None)
    app.display.ascii_preview = False
    app.listener = FakeVol()
    app.nudge_volume(1)
    assert app.listener.calls == ["+"]
    assert app._osd_volume
    # Magnitude is honoured: a fast turn (|delta|=3) moves 3 steps, not 1.
    app.nudge_volume(-3)
    assert app.listener.calls == ["+", "-", "-", "-"]


def test_now_playing_scroll_is_volume():
    app = build_sim_app(frames_dir=None)
    app.display.ascii_preview = False
    app.listener = FakeVol()
    app.store.apply_pushstate({"status": "play", "service": "mpd"})
    app.go("modern")
    app.fsm.current.handle_scroll(1)
    assert app.listener.calls == ["+"]


def test_volume_command_routes_through_nudge():
    app = build_sim_app(frames_dir=None)
    app.display.ascii_preview = False
    app.listener = FakeVol()
    app.handle("volume", "+")
    app.handle("volume", "-")
    assert app.listener.calls == ["+", "-"]


def test_mute_command_sets_mute():
    app = build_sim_app(frames_dir=None)
    app.display.ascii_preview = False
    app.listener = FakeVol()
    app.handle("mute")
    assert app.listener.calls == ["mute"]


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print("\nAll %d volume tests passed." % len(tests))


if __name__ == "__main__":
    main()
