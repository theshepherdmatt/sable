"""Rotary sampling must never block on the callbacks, and a fast spin must
arrive coalesced.

The bug these pin: on_scroll used to be called inline in the 1ms sampling loop.
On the now-playing screen that callback sends volume commands over socket.io and
repaints the panel over SPI (~16ms for a full frame), so sampling stalled for
tens of polls and quadrature edges that happened meanwhile were lost -- felt as
detents that did nothing, and as a decoder left mid-sequence so the next detent
was wrong too.

No GPIO here: these drive the dispatch side directly, the way the sampling
thread does, so they run anywhere.
"""
import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sable.inputs.rotary import QuadratureDecoder, RotaryEncoder, accelerate


class _Pins:
    clk, dt, sw = 13, 5, 6
    long_press_s = 2.5
    reverse = False


def _encoder(on_scroll=None, on_select=None, on_long=None):
    """A RotaryEncoder with only its dispatch side wired up (no GPIO)."""
    enc = RotaryEncoder(_Pins(), on_scroll or (lambda d: None),
                        on_select or (lambda: None), on_long or (lambda: None))
    enc._acc_lock = threading.Lock()
    enc._wake = threading.Event()
    enc._running = True
    threading.Thread(target=enc._dispatch, daemon=True).start()
    return enc


def _turn(enc, steps):
    """Simulate the sampling thread seeing `steps` detents back to back."""
    for s in steps:
        with enc._acc_lock:
            enc._scroll_acc += s
        enc._wake.set()


def test_fast_spin_arrives_coalesced_not_one_call_per_detent():
    calls = []
    enc = _encoder(on_scroll=lambda d: calls.append(d))
    try:
        _turn(enc, [1] * 8)
        time.sleep(0.4)
    finally:
        enc._running = False
    assert sum(calls) == 8, "steps were lost or duplicated: %r" % (calls,)
    assert len(calls) < 8, (
        "a fast spin produced %d separate callbacks (one per detent); it "
        "should coalesce" % len(calls))


def test_slow_callback_does_not_lose_steps():
    """The real failure mode: a callback slower than the turn. Steps arriving
    while it runs must still be delivered, not dropped."""
    calls = []

    def slow(d):
        calls.append(d)
        time.sleep(0.15)            # stands in for socket + SPI repaint

    enc = _encoder(on_scroll=slow)
    try:
        _turn(enc, [1] * 5)
        time.sleep(0.1)
        _turn(enc, [1] * 5)          # arrives while `slow` is still running
        time.sleep(1.0)
    finally:
        enc._running = False
    assert sum(calls) == 10, "lost steps during a slow callback: %r" % (calls,)


def test_direction_changes_net_out():
    calls = []
    enc = _encoder(on_scroll=lambda d: calls.append(d))
    try:
        _turn(enc, [1, 1, 1, -1, -1])
        time.sleep(0.4)
    finally:
        enc._running = False
    assert sum(calls) == 1


def test_button_events_still_fire():
    seen = []
    enc = _encoder(on_select=lambda: seen.append("select"),
                   on_long=lambda: seen.append("long"))
    try:
        enc._buttons.append("select")
        enc._buttons.append("long")
        enc._wake.set()
        time.sleep(0.4)
    finally:
        enc._running = False
    assert seen == ["select", "long"]


def test_decoder_still_yields_one_step_per_detent():
    """Guard the decoder itself: the dispatch rework must not have changed it."""
    # From the rest position (1,1): (1,0)->(0,0)->(0,1)->(1,1) is one detent
    # forward, and the mirror sequence is one detent back.
    fwd = QuadratureDecoder()
    fwd.update(1, 1)
    assert sum(fwd.update(*p) for p in [(1, 0), (0, 0), (0, 1), (1, 1)]) == +1

    back = QuadratureDecoder()
    back.update(1, 1)
    assert sum(back.update(*p) for p in [(0, 1), (0, 0), (1, 0), (1, 1)]) == -1

    assert accelerate(0.05) == 4 and accelerate(0.5) == 1


if __name__ == "__main__":
    failed = 0
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
                print("PASS", _name)
            except AssertionError as exc:
                failed += 1
                print("FAIL", _name, "->", exc)
    print("%d failure(s)" % failed)
    sys.exit(1 if failed else 0)
