"""Rotary encoder: a pure quadrature decoder (unit-testable) plus a real
RPi.GPIO polling driver (NOT started in Phase 0 -- it would claim BCM13/5/6,
which the live plugin's rotary thread already owns).

Contract: rotate = scroll (NEVER volume); short press = select; long-press 2.5s =
in-menu return to clock else back. Detent acceleration on fast turns.
"""
import time

# state index = (prev<<2)|cur, where each half is (clk<<1)|dt.
_TBL = {
    0b0001: +1, 0b0111: +1, 0b1110: +1, 0b1000: +1,
    0b0010: -1, 0b0100: -1, 0b1101: -1, 0b1011: -1,
}


class QuadratureDecoder:
    """Feed raw (clk, dt) reads; get -1/0/+1 once per detent."""

    def __init__(self):
        self.state = None
        self.acc = 0

    def update(self, clk, dt):
        s = ((clk & 1) << 1) | (dt & 1)
        if self.state is None:
            self.state = s
            return 0
        self.acc += _TBL.get((self.state << 2) | s, 0)
        self.state = s
        if s == 0b11:  # at a detent rest position
            step = 0
            if self.acc >= 3:
                step = +1
            elif self.acc <= -3:
                step = -1
            self.acc = 0
            return step
        return 0


def accelerate(dt_seconds):
    """Detent-to-detent interval -> step multiplier (carry-forward acceleration)."""
    if dt_seconds < 0.08:
        return 4
    if dt_seconds < 0.15:
        return 2
    return 1


class RotaryEncoder:
    """Real GPIO driver. Construct + start() only on hardware (never in the slice)."""

    def __init__(self, pins, on_scroll, on_select, on_long_press, poll_s=0.001):
        self.pins = pins
        self.on_scroll = on_scroll
        self.on_select = on_select
        self.on_long_press = on_long_press
        self.poll_s = poll_s
        self._running = False
        self._decoder = QuadratureDecoder()
        self._last_detent = 0.0
        self._reverse = getattr(pins, "reverse", False)
        # Sampling and dispatch are SEPARATE threads.
        #
        # The callbacks used to run inline in the polling loop, and they are not
        # cheap: a scroll on the now-playing screen sends volume commands over
        # socket.io and then repaints the panel over SPI (~16ms for a full
        # frame). At a 1ms sample interval that stalls sampling for tens of
        # polls, and quadrature edges that occur meanwhile are gone for good --
        # felt as detents that do nothing, and as a decoder left mid-sequence
        # so the NEXT detent is wrong too ("sticking"). The sampling loop must
        # do nothing but read pins and decode.
        self._scroll_acc = 0
        self._acc_lock = None      # created in start(), needs threading
        self._wake = None
        self._buttons = []

    def start(self):
        import RPi.GPIO as GPIO
        import threading

        self._GPIO = GPIO
        GPIO.setmode(GPIO.BCM)
        for pin in (self.pins.clk, self.pins.dt, self.pins.sw):
            GPIO.setup(pin, GPIO.IN, pull_up_down=GPIO.PUD_UP)
        self._acc_lock = threading.Lock()
        self._wake = threading.Event()
        self._running = True
        threading.Thread(target=self._loop, daemon=True,
                         name="sable-rotary").start()
        threading.Thread(target=self._dispatch, daemon=True,
                         name="sable-rotary-dispatch").start()

    def _loop(self):
        """SAMPLING ONLY. Never calls a callback -- see __init__."""
        g = self._GPIO
        press_started = None
        import os
        _dbg = os.environ.get("SABLE_ROTARY_DEBUG")
        while self._running:
            step = self._decoder.update(g.input(self.pins.clk), g.input(self.pins.dt))
            if step:
                now = time.monotonic()
                gap = now - self._last_detent
                mult = accelerate(gap)
                self._last_detent = now
                emit = step * mult
                if self._reverse:
                    emit = -emit
                if _dbg:
                    print("ROTARY step=%+d gap=%.3fs mult=%d rev=%d emit=%+d"
                          % (step, gap, mult, self._reverse, emit), flush=True)
                with self._acc_lock:
                    self._scroll_acc += emit
                self._wake.set()
            pressed = g.input(self.pins.sw) == 0
            if pressed and press_started is None:
                press_started = time.monotonic()
            elif not pressed and press_started is not None:
                held = time.monotonic() - press_started
                press_started = None
                self._buttons.append("long" if held >= self.pins.long_press_s
                                     else "select")
                self._wake.set()
            time.sleep(self.poll_s)

    def _dispatch(self):
        """Runs the callbacks off the sampling thread, and COALESCES.

        Draining the accumulator means a fast spin becomes one on_scroll(+7)
        rather than seven separate calls each doing its own socket round-trip
        and repaint. That is what makes a quick twist land as one smooth move
        instead of a queue of updates arriving after you have stopped turning.
        """
        while self._running:
            self._wake.wait(0.2)
            self._wake.clear()
            with self._acc_lock:
                delta, self._scroll_acc = self._scroll_acc, 0
            if delta:
                try:
                    self.on_scroll(delta)
                except Exception:
                    pass
            while self._buttons:
                which = self._buttons.pop(0)
                try:
                    if which == "long":
                        self.on_long_press()
                    else:
                        self.on_select()
                except Exception:
                    pass

    def stop(self):
        self._running = False
        try:
            self._GPIO.cleanup()
        except Exception:
            pass
