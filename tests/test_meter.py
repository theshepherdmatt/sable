"""Phase 3 self-tests: CAVA ascii parsing, bar smoothing/decay, position guards.

Pure logic, no fifo and no cava. Run: python3 tests/test_meter.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sable.display.fifo_meter import FifoBars, BarSmoother
from sable.state import StateStore


def test_fifo_ascii_parse_latest_frame():
    fb = FifoBars(path="/nonexistent", bars=4, log=lambda *a: None)
    # two frames; the second (latest complete) wins; trailing partial is kept.
    fb._consume("0;0;0;0\n255;128;0;64\n12;13")
    assert fb._last == [1.0, 128 / 255.0, 0.0, 64 / 255.0]
    assert fb._buf == "12;13"


def test_fifo_ignores_incomplete_frame():
    fb = FifoBars(path="/nonexistent", bars=3, log=lambda *a: None)
    fb._consume("10;20;30")            # no newline yet
    assert fb._last == [0.0, 0.0, 0.0]
    fb._consume(";\n")                 # completes the frame
    assert fb._last == [10 / 255.0, 20 / 255.0, 30 / 255.0]


def test_smoother_rise_and_decay():
    s = BarSmoother(1, attack=0.5, decay=0.1)
    assert s.update([1.0])[0] == 0.5          # rise smoothed from 0
    assert abs(s.update([1.0])[0] - 0.75) < 1e-9
    s2 = BarSmoother(1, attack=1.0, decay=0.1)
    s2.update([1.0])                          # -> 1.0
    assert abs(s2.update([0.0])[0] - 0.9) < 1e-9   # falls by decay, not instant
    assert abs(s2.update([0.0])[0] - 0.8) < 1e-9


def test_position_freezes_on_pause():
    s = StateStore(log=lambda *a: None)
    s.apply_pushstate({"status": "pause", "seek": 70000, "duration": 233})
    assert s.live_position_ms() == 70000      # no drift while paused


def test_position_clamps_at_duration():
    s = StateStore(log=lambda *a: None)
    s.apply_pushstate({"status": "play", "seek": 999000, "duration": 100})
    assert s.live_position_ms() == 100 * 1000  # clamped at track end


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print("\nAll %d meter tests passed." % len(tests))


if __name__ == "__main__":
    main()
