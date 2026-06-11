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

    @staticmethod
    def _hgrad(w, h, left, right):
        """A horizontal 2-stop grey gradient image (left->right), built by
        resizing a 2x1 ramp. Used for the selection bar."""
        stop = Image.new("L", (2, 1), 0)
        stop.putpixel((0, 0), left)
        stop.putpixel((1, 0), right)
        return stop.resize((max(1, w), max(1, h)), Image.BILINEAR)

    def draw_menu_surface(self, canvas, draw, w, h, header, rows, index,
                          key_prefix="m", top=16, row_h=16, nrows=3):
        """Shared 'designed list' surface for the menu and browser: a dim header,
        a gradient selection bar with a bright left accent (dark text on it), and
        an optional right-aligned value/chevron per row. Long labels scroll
        (clipped). rows = list of (label, value_or_None)."""
        self.text(canvas, (4, 1), (header or "").upper(),
                  self.app.fonts.get("sans_bold", 10), fill=120)
        label_font = self.app.fonts.get("sans", 12)
        val_font = self.app.fonts.get("sans", 10)
        if not rows:
            self.text(canvas, (8, top + row_h), "(empty)", label_font, fill=110)
            return
        start = max(0, min(index - 1, max(0, len(rows) - nrows)))
        for i in range(start, min(start + nrows, len(rows))):
            y = top + (i - start) * row_h
            selected = i == index
            label, value = rows[i]
            if selected:
                canvas.paste(self._hgrad(w, row_h - 1, 255, 150), (0, y - 1))
                draw.rectangle((0, y - 1, 1, y + row_h - 3), fill=255)  # accent
                fg, vg = 0, 55
            else:
                fg, vg = 220, 110
            reserve = 0
            if value:
                vw = self.text_width(draw, value, val_font)
                self.text(canvas, (w - vw - 5, y + 1), value, val_font, fill=vg)
                reserve = vw + 9
            self.draw_text_clipped(canvas, "%s_%d" % (key_prefix, i), label,
                                   label_font, 8, y, w - 8 - reserve, fill=fg)

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
