"""The single spectrum / VU meter screen.

ONE class, configurable by `style` (bars | dots) and `bars` count -- the old code
had vu_screen AND digitalvu_screen as two implementations of the same meter.
Reads from the shared FifoBars; smoothing/decay comes from the shared BarSmoother.
Bars can also be injected (feed()) for headless pure-render tests.
"""
from .base import Screen
from ..display.fifo_meter import DISPLAY_FIFO, FifoBars, BarSmoother


class MeterScreen(Screen):
    name = "spectrum"

    def __init__(self, app, style=None, bars=24, attack=0.5, decay=0.08):
        super().__init__(app)
        self.style = style
        self.bars = bars
        self.reader = FifoBars(DISPLAY_FIFO, bars, log=app.log)
        self.smoother = BarSmoother(bars, attack, decay)
        self.test_bars = None      # injected for pure-render tests

    def feed(self, bars):
        self.test_bars = list(bars)

    def _raw(self):
        return self.test_bars if self.test_bars is not None else self.reader.read()

    def on_exit(self):
        self.reader.close()

    def handle_select(self):
        self.app.fsm.dispatch("menu")

    def handle_back(self):
        self.app.fsm.dispatch("back")

    def render(self, canvas, draw, w, h):
        st = self.app.store.get()
        style = self.style or self.app.settings.get("display", "spectrum_style",
                                                     default="bars")
        vals = self.smoother.update(self._raw())

        # title strip (scrolls if wide)
        self.draw_text_clipped(canvas, "sp_title", st.title or "(no title)",
                               self.app.fonts.get("sans", 10), 2, 0, w - 4)

        # bars region
        top = 12
        region = h - top
        n = len(vals)
        if n == 0:
            return
        gap = 1
        bw = max(1, (w - (n + 1) * gap) // n)
        x = gap
        for v in vals:
            bh = int(max(0.0, min(1.0, v)) * (region - 1))
            ytop = h - 1 - bh
            if style == "dots":
                draw.rectangle((x, ytop, x + bw - 1, min(h - 1, ytop + 1)), fill=255)
            else:  # bars
                draw.rectangle((x, ytop, x + bw - 1, h - 1), fill=255)
            x += bw + gap
