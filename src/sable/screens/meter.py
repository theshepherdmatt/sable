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
import math
import os

from PIL import Image, ImageDraw

from .base import Screen
from ..display.fifo_meter import DISPLAY_FIFO, FifoBars, BarSmoother

_STYLES = ("bars", "dots", "mirror", "ribbon", "vu")

# The analog VU dial face (carried over from Quadify's vuscreen -- already 256x64,
# the same panel). The needle geometry below is matched to this artwork.
_DIAL_PATH = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "assets", "vuscreen.png"))


class MeterScreen(Screen):
    name = "spectrum"
    TOP = 22                       # meter region: below the 2-line header (title + format)

    def __init__(self, app, style=None, bars=24, attack=0.5, decay=0.08):
        super().__init__(app)
        self.style = style
        self.bars = bars
        self.reader = FifoBars(DISPLAY_FIFO, bars, log=app.log)
        self.smoother = BarSmoother(bars, attack, decay)
        self.test_bars = None
        self._peaks = [0.0] * bars
        self._peak_decay = 0.02
        # Analog VU style: twin needles with classic ballistics (fast attack, slow
        # release) reading L/R from the two halves of the bar array. Geometry +
        # 0.8/0.15 ballistics carried over from Quadify's proven vuscreen.
        self._left_vu = 0.0
        self._right_vu = 0.0
        self._vu_attack = 0.8
        self._vu_release = 0.15
        # Geometry matched to assets/vuscreen.png (twin arcs, scale -20..+6). The
        # pivot sits below the panel; the needle sweeps +/- _vu_swing degrees from
        # vertical to track the printed arc.
        self._vu_len = 50
        self._vu_swing = 46                        # degrees each side of vertical
        self._vu_centres = ((67, 88), (191, 88))   # L/R pivots (off the bottom edge)
        self._dial = None                          # cached dial face | False

    def feed(self, bars):
        self.test_bars = list(bars)

    def _raw(self):
        return self.test_bars if self.test_bars is not None else self.reader.read()

    def _fmt_line(self, st):
        """Format readout from the live Volumio state: 'samplerate · bitdepth'
        (e.g. '96 kHz · 24 bit'). Both are pre-formatted display strings from
        Volumio; drop either field gracefully when absent."""
        parts = [p.strip() for p in (st.samplerate, st.bitdepth) if p and p.strip()]
        return " · ".join(parts)

    def _draw_line(self, canvas, draw, key, text, font, y, fill, w):
        """Centre `text` horizontally if it fits the panel; otherwise fall back to
        the screen's existing clipped marquee (so long titles keep scrolling)."""
        if not text:
            return
        tw = self.text_width(draw, text, font)
        if tw <= w - 4:
            self.text(canvas, ((w - tw) // 2, y), text, font, fill=fill)
        else:
            self.draw_text_clipped(canvas, key, text, font, 2, y, w - 4, fill=fill)

    def on_exit(self):
        self.reader.close()

    def handle_scroll(self, delta):
        self.app.nudge_volume(delta)      # turning the knob on now-playing = volume

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

        if style == "vu":
            self._render_vu(canvas, draw, w, h, st)
            return
        self._draw_line(canvas, draw, "sp_title", st.title or "(no title)",
                        self.app.fonts.get("sans", 10), 0, 160, w)
        self._draw_line(canvas, draw, "sp_fmt", self._fmt_line(st),
                        self.app.fonts.get("sans", 8), 11, 110, w)
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

    # --- analog VU (twin needles over the dial face) --------------------------
    def _get_dial(self, w, h):
        if self._dial is None:
            try:
                img = Image.open(_DIAL_PATH).convert("L")
                if img.size != (w, h):
                    img = img.resize((w, h), Image.LANCZOS)
                self._dial = img.point(lambda p: int(p * 0.6))   # dim so needles pop
            except Exception as e:
                self.app.log("meter: VU dial load failed:", e)
                self._dial = False
        return self._dial if self._dial is not False else None

    def _vu_needle(self, draw, cx, cy, level):
        # level 0..1 -> -swing..+swing deg; -90 puts 0-level at upper-left, full at
        # upper-right (classic VU swing). Pivot sits below the panel.
        s = self._vu_swing
        ang = math.radians((-s + max(0.0, min(1.0, level)) * 2 * s) - 90.0)
        x = cx + self._vu_len * math.cos(ang)
        y = cy + self._vu_len * math.sin(ang)
        draw.line((cx, cy, x, y), fill=255, width=2)

    def _render_vu(self, canvas, draw, w, h, st):
        dial = self._get_dial(w, h)
        if dial is not None:
            canvas.paste(dial, (0, 0))
        raw = self._raw()
        n = len(raw)
        if n:
            half = max(1, n // 2)
            rl = sum(raw[:half]) / half
            rr = sum(raw[half:]) / max(1, n - half)
        else:
            rl = rr = 0.0
        # VU ballistics: fast attack, slow release (the needle swings, not twitches)
        al = self._vu_attack if rl > self._left_vu else self._vu_release
        ar = self._vu_attack if rr > self._right_vu else self._vu_release
        self._left_vu = al * rl + (1 - al) * self._left_vu
        self._right_vu = ar * rr + (1 - ar) * self._right_vu
        (lx, ly), (rx, ry) = self._vu_centres
        self._vu_needle(draw, lx, ly, self._left_vu)
        self._vu_needle(draw, rx, ry, self._right_vu)
        # my own touch: a dim track title in the empty band above the dials, a
        # smaller format readout beneath it, and quiet L/R channel tags so the
        # twin meters read as stereo. Both header lines sit above the needle
        # sweep (tips reach y~38), so they never cross the dial.
        self._draw_line(canvas, draw, "vu_title", st.title or "",
                        self.app.fonts.get("sans", 9), 0, 150, w)
        self._draw_line(canvas, draw, "vu_fmt", self._fmt_line(st),
                        self.app.fonts.get("sans", 8), 11, 110, w)
        ch = self.app.fonts.get("sans_bold", 8)
        self.text(canvas, (lx - 2, h - 9), "L", ch, fill=120)
        self.text(canvas, (rx - 2, h - 9), "R", ch, fill=120)
