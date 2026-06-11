"""The spectrum / VU visualiser screen.

ONE configurable screen: style = bars | dots | mirror | ribbon, reading the shared
FifoBars (smoothed by BarSmoother). Now that the panel is 16-grey, every style
uses grey GRADIENTS and a bright peak-hold cap instead of flat white blocks, so it
reads like a hi-fi meter, not a status bar:
  bars    upright gradient columns (bright tip -> dim base) + falling peak caps
  dots    floating LED-style caps at each band's level
  mirror  bars grown up AND down from a centre line, bright at the centre
  ribbon  a filled spectral envelope with a vertical grey wash + bright crest

Bars can be injected (feed()) for headless pure-render tests.
"""
from PIL import Image, ImageDraw

from .base import Screen
from ..display.fifo_meter import DISPLAY_FIFO, FifoBars, BarSmoother

_STYLES = ("bars", "dots", "mirror", "ribbon")


class MeterScreen(Screen):
    name = "spectrum"
    TOP = 12                       # meter region starts below the title strip

    def __init__(self, app, style=None, bars=24, attack=0.5, decay=0.08):
        super().__init__(app)
        self.style = style
        self.bars = bars
        self.reader = FifoBars(DISPLAY_FIFO, bars, log=app.log)
        self.smoother = BarSmoother(bars, attack, decay)
        self.test_bars = None
        self._peaks = [0.0] * bars
        self._peak_decay = 0.02

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

    def _resolve_style(self):
        s = self.style or self.app.settings.get("display", "spectrum_style",
                                                 default="bars")
        return s if s in _STYLES else "bars"

    def _update_peaks(self, vals):
        if len(self._peaks) != len(vals):
            self._peaks = [0.0] * len(vals)
        for i, v in enumerate(vals):
            if v >= self._peaks[i]:
                self._peaks[i] = v
            else:
                self._peaks[i] = max(v, self._peaks[i] - self._peak_decay)
        return self._peaks

    @staticmethod
    def _vbar(bw, bh, top_grey, bot_grey):
        """A vertical 2-stop gradient (top_grey at the top row, bot_grey at the
        bottom) built by resizing a 1x2 ramp -- cheap and smooth."""
        if bh <= 0:
            return None
        stop = Image.new("L", (1, 2), 0)
        stop.putpixel((0, 0), top_grey)
        stop.putpixel((0, 1), bot_grey)
        return stop.resize((max(1, bw), bh), Image.BILINEAR)

    # --- render ---
    def render(self, canvas, draw, w, h):
        st = self.app.store.get()
        style = self._resolve_style()
        vals = self.smoother.update(self._raw())
        peaks = self._update_peaks(vals)

        self.draw_text_clipped(canvas, "sp_title", st.title or "(no title)",
                               self.app.fonts.get("sans", 10), 2, 0, w - 4, fill=160)
        if not vals:
            return
        if style == "ribbon":
            self._render_ribbon(canvas, draw, w, h, vals)
        elif style == "mirror":
            self._render_mirror(canvas, draw, w, h, vals, peaks)
        else:
            self._render_bars(canvas, draw, w, h, vals, peaks, dots=(style == "dots"))

    def _render_bars(self, canvas, draw, w, h, vals, peaks, dots=False):
        top = self.TOP
        region = h - top
        n = len(vals)
        gap = 1
        bw = max(1, (w - (n + 1) * gap) // n)
        x = gap
        for i, v in enumerate(vals):
            bh = int(max(0.0, min(1.0, v)) * (region - 1))
            ytop = h - 1 - bh
            x1 = x + bw - 1
            if dots:
                if bh > 0:
                    draw.rectangle((x, max(top, ytop - 1), x1, min(h - 1, ytop + 1)),
                                   fill=235)
            else:
                bar = self._vbar(bw, bh, 245, 110)
                if bar is not None:
                    canvas.paste(bar, (x, ytop))
                    draw.line((x, ytop, x1, ytop), fill=255)      # bright crest
            pkh = int(max(0.0, min(1.0, peaks[i])) * (region - 1))
            if pkh > bh + 1:
                pky = h - 1 - pkh
                draw.line((x, pky, x1, pky), fill=175)            # falling peak cap
            x += bw + gap

    def _render_mirror(self, canvas, draw, w, h, vals, peaks):
        top = self.TOP
        region = h - top
        mid = top + region // 2
        half = region // 2 - 1
        n = len(vals)
        gap = 1
        bw = max(1, (w - (n + 1) * gap) // n)
        x = gap
        for i, v in enumerate(vals):
            bh = int(max(0.0, min(1.0, v)) * half)
            x1 = x + bw - 1
            if bh > 0:
                up = self._vbar(bw, bh, 130, 235)        # dim tip -> bright centre
                canvas.paste(up, (x, mid - bh))
                down = self._vbar(bw, bh, 235, 130)       # bright centre -> dim tip
                canvas.paste(down, (x, mid))
                draw.line((x, mid - bh, x1, mid - bh), fill=255)
                draw.line((x, mid + bh, x1, mid + bh), fill=255)
            else:
                draw.line((x, mid, x1, mid), fill=90)     # quiet baseline
            x += bw + gap

    def _render_ribbon(self, canvas, draw, w, h, vals):
        top = self.TOP
        region = h - top
        n = len(vals)
        pts = []
        for i, v in enumerate(vals):
            xx = 0 if n == 1 else int(i * (w - 1) / (n - 1))
            yy = (h - 1) - int(max(0.0, min(1.0, v)) * (region - 1))
            pts.append((xx, yy))
        # filled envelope: a vertical wash, masked to the area under the crest
        mask = Image.new("1", (w, h), 0)
        ImageDraw.Draw(mask).polygon(pts + [(w - 1, h - 1), (0, h - 1)], fill=1)
        wash = self._vbar(w, region, 205, 35)
        if wash is not None:
            canvas.paste(wash, (0, top), mask.crop((0, top, w, h)))
        draw.line(pts, fill=255, width=1)                 # bright crest
