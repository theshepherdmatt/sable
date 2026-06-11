"""Slice 3 self-tests: the idle journey ladder (awake -> dim -> dark) + wake.

Pure logic against the App with the sim display. Drives tick_idle with controlled
timestamps; no real GPIO/SPI. Run: python3 tests/test_idle.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sable.app import build_sim_app
from sable import hardware


def _app():
    app = build_sim_app(frames_dir=None)
    app.display.ascii_preview = False
    app.go("clock")
    # fast thresholds for the test; in-memory only (no disk write)
    app.settings._data["screensaver"] = {"clock_after_s": 999, "dim_s": 2, "idle_s": 5}
    app.set_base_contrast(hardware.CONTRAST.high)
    app.last_input = 0.0
    return app


def test_awake_then_dim_then_dark():
    app = _app()
    base = app._base_contrast
    app.tick_idle(1.0)                 # < dim_s
    assert app._idle_tier == "awake"
    assert app._cur_contrast == base
    app.tick_idle(3.0)                 # >= dim_s, < idle_s
    assert app._idle_tier == "dim"
    assert app._cur_contrast < base    # dimmed
    assert app.asleep is False
    for _ in range(40):                # past idle_s: fade to dark over ticks
        app.tick_idle(6.0)
    assert app.asleep is True
    assert app._cur_contrast == 0


def test_wake_restores_base_and_unsleeps():
    app = _app()
    for _ in range(40):
        app.tick_idle(6.0)
    assert app.asleep is True
    app.note_activity()
    assert app.asleep is False
    assert app._idle_tier == "awake"
    assert app._cur_contrast == app._base_contrast


def test_playing_never_dims_or_sleeps():
    app = _app()
    app.store.apply_pushstate({"status": "play"})
    app.tick_idle(1000.0)              # way past idle_s
    assert app._idle_tier == "awake"
    assert app.asleep is False


def test_dim_s_zero_disables_dim_tier():
    app = _app()
    app.settings._data["screensaver"]["dim_s"] = 0
    app.tick_idle(3.0)
    assert app._idle_tier == "awake"


def test_paused_nowplaying_stays_bright_until_clock():
    app = _app()
    app.store.apply_pushstate({"status": "play"})
    app.go("modern")
    app.store.apply_pushstate({"status": "pause"})     # arms the pause->clock timer
    app.last_input = 0.0
    app.tick_idle(100.0)                                # well past dim_s, but paused
    assert app.fsm.current.name in ("modern", "spectrum")
    assert app._idle_tier == "awake"                    # NOT dimmed while paused
    assert app._pause_idle is False


def test_paused_falls_to_clock_after_grace():
    app = _app()
    app.store.apply_pushstate({"status": "play"})
    app.go("modern")
    app.store.apply_pushstate({"status": "pause"})
    assert app.fsm.current.name in ("modern", "spectrum")
    app._pause_timer_fire()                             # grace elapsed
    assert app.fsm.current.name == "clock"
    assert app._pause_idle is True
    # resuming clears the idle-clock flag (reconcile then restores now-playing)
    app.store.apply_pushstate({"status": "play"})
    assert app._pause_idle is False


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print("\nAll %d idle tests passed." % len(tests))


if __name__ == "__main__":
    main()
