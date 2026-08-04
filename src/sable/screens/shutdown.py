"""The power-off message.

Deliberately STATIC and deliberately not the boot splash. Shutdown reused
SplashScreen with its subtitle swapped, which meant the wordmark and the
sweeping "warming up" scanner kept animating while the Pi powered down -- a
progress indicator implying work is in flight, on a machine that is about to
stop. A single centred line on an otherwise cleared panel says the one thing
worth saying, and the absence of motion is itself the signal that this is the
end of the sequence rather than the start of one.

The panel is blanked a moment after this renders (see App._shutdown_sequence),
so nothing here needs to survive or animate.
"""
from .base import Screen


class ShutdownScreen(Screen):
    name = "shutdown"

    def render(self, canvas, draw, w, h):
        self.text(canvas, (w // 2, h // 2 - 7), "SHUTTING DOWN",
                  self.app.fonts.get("sans_bold", 16), fill=255, anchor="mm")
        self.text(canvas, (w // 2, h // 2 + 11), "please wait",
                  self.app.fonts.get("sans", 9), fill=120, anchor="mm")
