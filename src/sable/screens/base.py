"""Shared Screen base class.

A state IS a screen. The base owns the mechanics the OLD code copy-pasted into
every screen (its own state_lock, update_event, background draw thread, and a
private _read_fifo): here there is ONE render tick driven by the app, and ONE
marquee implementation. Screens declare WHAT to draw; the base provides the
cadence and the scrolling.

render() receives the canvas image (for paste/clip) AND a bound ImageDraw.
"""
import time

from PIL import Image, ImageDraw


def marquee_offset(start, now, total, speed=30.0):
    """Time-based scroll offset. No per-screen thread: position is a pure
    function of wall time, so the app's single render tick animates it."""
    return int((now - start) * speed) % max(1, total)


class Screen:
    name = "screen"

    def __init__(self, app):
        self.app = app
        self._scroll = {}   # key -> (text, start_monotonic)

    def on_enter(self, **kwargs):
        pass

    def on_exit(self):
        pass

    def render(self, canvas, draw, w, h):
        pass

    def handle_scroll(self, delta):
        pass

    def handle_select(self):
        pass

    def handle_back(self):
        pass

    def on_state(self, state):
        pass

    def tick(self, now):
        pass

    # --- shared text helpers ---
    def text_width(self, draw, text, font):
        try:
            return int(draw.textlength(text, font=font))
        except Exception:
            b = font.getbbox(text)
            return b[2] - b[0]

    def text(self, canvas, xy, text, font, fill=255, anchor=None):
        """Crisp (non-antialiased) text on the greyscale canvas.

        Glyph SHAPE comes from a 1-bit mask -- hard edges, exactly as sharp as
        the old 1-bit panel mode -- and the chosen grey `fill` is pasted THROUGH
        that mask. So we keep type hierarchy (white title / grey artist / dim
        meta) with no soft AA halo, which a truetype draw straight onto an "L"
        canvas would add. anchor is honoured by ImageDraw on the mask."""
        if not text:
            return
        mask = Image.new("1", canvas.size, 0)
        ImageDraw.Draw(mask).text(xy, text, font=font, fill=1, anchor=anchor)
        canvas.paste(fill, (0, 0), mask)

    def draw_text_clipped(self, canvas, key, text, font, x, y, max_width,
                          fill=255, gap=24, speed=30.0, height=None):
        """Draw `text` at (x, y) within max_width. If it fits, draw plainly; if
        not, scroll it (marquee), clipped to the box so it never bleeds into other
        regions. Scrolling state is keyed and time-based (no thread). Rendered
        crisp via a 1-bit mask (see text()), so the marquee stays sharp on the
        greyscale canvas."""
        w = self.text_width(ImageDraw.Draw(canvas), text, font)
        if height is None:
            try:
                asc, desc = font.getmetrics()
                height = asc + desc
            except Exception:
                height = font.size + 2
        if w <= max_width:
            mask = Image.new("1", (max_width, height), 0)
            ImageDraw.Draw(mask).text((0, 0), text, font=font, fill=1)
            canvas.paste(fill, (x, y), mask)
            return
        total = w + gap
        now = time.monotonic()
        st = self._scroll.get(key)
        if st is None or st[0] != text:
            self._scroll[key] = (text, now)
            off = 0
        else:
            off = marquee_offset(st[1], now, total, speed)
        mask = Image.new("1", (max_width, height), 0)
        sd = ImageDraw.Draw(mask)
        sd.text((-off, 0), text, font=font, fill=1)
        sd.text((-off + total, 0), text, font=font, fill=1)
        canvas.paste(fill, (x, y), mask)
