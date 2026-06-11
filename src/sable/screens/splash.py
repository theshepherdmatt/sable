"""Boot splash -- a piece of hi-fi turning on, held through the Volumio + NTP gates.

The wordmark sits over a baseline with a bright scanner that sweeps back and forth
(a "warming up" indicator) while we wait for Volumio and the clock; the gate status
shows beneath. The app's render tick drives the animation, so position is a pure
function of time since on_enter -- no per-screen thread.
"""
import math
import time

from .base import Screen


class SplashScreen(Screen):
    name = "splash"

    def __init__(self, app):
        super().__init__(app)
        self.subtitle = "starting"
        self._t0 = time.monotonic()

    def on_enter(self, **kwargs):
        self._t0 = time.monotonic()

    def render(self, canvas, draw, w, h):
        t = time.monotonic() - self._t0
        self.text(canvas, (w // 2, 23), "SABLE",
                  self.app.fonts.get("sans_bold", 30), fill=255, anchor="mm")

        # baseline + traveling scanner blob (warming-up indicator)
        bx0, bx1, by = w // 2 - 72, w // 2 + 72, 42
        draw.line((bx0, by, bx1, by), fill=45)
        span = bx1 - bx0
        pos = math.sin(t * 2.2) * 0.5 + 0.5          # 0..1 ping-pong
        cx = int(bx0 + pos * span)
        reach = 14
        for dx in range(-reach, reach + 1):
            x = cx + dx
            if bx0 <= x <= bx1:
                g = int(255 * max(0.0, 1.0 - abs(dx) / reach))
                if g > 45:
                    draw.line((x, by, x, by + 1), fill=g)

        self.text(canvas, (w // 2, h - 9), (self.subtitle or "").upper(),
                  self.app.fonts.get("sans", 9), fill=110, anchor="mm")
