"""The hero now-playing screen ("Panel").

Greyscale album art, full panel height, BLEEDS (via a short fade) into a text
field with a clear type hierarchy: white title, grey artist, dim meta. A DIM live
spectrum floor along the bottom turns this into now-playing-AND-spectrum without a
mode switch -- drawn only when the source actually feeds CAVA, so non-MPD sources
just omit it rather than show a flat line. Paused is a DESIGNED state (dimmed art +
a calm pause mark + a frozen progress hairline so you keep your place), never the
old blank panel.

Everything reads from StateStore; the app's single render tick animates the
marquee (Screen base), the live seek bar (store.live_position_ms) and the floor.
Text is drawn crisp via Screen.text()/draw_text_clipped() (1-bit mask + grey
paste), so it stays sharp on the greyscale canvas.
"""
from PIL import Image

from .base import Screen
from ..display.fifo_meter import DISPLAY_FIFO, FifoBars, BarSmoother


def _mmss(seconds):
    seconds = max(0, int(seconds))
    return "%d:%02d" % (seconds // 60, seconds % 60)


class ModernScreen(Screen):
    name = "modern"
    ART = 64
    FLOOR_BARS = 28
    FADE = 10               # art->field bleed width (px)

    def __init__(self, app):
        super().__init__(app)
        self._reader = None
        self._smoother = BarSmoother(self.FLOOR_BARS, attack=0.6, decay=0.12)
        self._test_floor = None     # injected for headless proof renders
        self._scrim = None          # cached Cinema bottom-up darkening scrim

    def feed_floor(self, bars):
        self._test_floor = list(bars)

    def on_exit(self):
        # Release the shared cava fifo so the meter screen can read it (only one
        # now-playing screen is ever active, so only one reader is open at a time).
        if self._reader is not None:
            self._reader.close()
            self._reader = None

    def handle_select(self):
        self.app.fsm.dispatch("menu")

    def handle_back(self):
        self.app.fsm.dispatch("back")

    # --- render ---
    def render(self, canvas, draw, w, h):
        st = self.app.store.get()
        paused = st.status == "pause"
        theme = self.app.settings.get("display", "theme", default="panel")
        if theme == "cinema":
            self._render_cinema(canvas, draw, w, h, st, paused)
            return
        self._render_panel(canvas, draw, w, h, st, paused)

    def _render_panel(self, canvas, draw, w, h, st, paused):
        aw = self.ART

        # --- album art, full height, bleeding into the text field ---
        art = self.app.albumart.get(st.albumart)
        if art is not None:
            a = art.convert("L")
            if paused:
                a = a.point(lambda p: int(p * 0.45))
            canvas.paste(a, (0, 0))
            self._bleed(canvas, a, aw, h)
        else:
            self._draw_art_placeholder(canvas, draw, aw, h, dim=paused)

        tx = aw + self.FADE + 2
        tw = w - tx - 2

        # --- type hierarchy (radio metadata is sparse -> fill it intelligently) ---
        line1, line2 = self._title_lines(st)
        self.draw_text_clipped(canvas, "title", line1,
                               self.app.fonts.get("sans_bold", 15), tx, 1, tw,
                               fill=125 if paused else 255)
        self.draw_text_clipped(canvas, "artist", line2,
                               self.app.fonts.get("sans", 11), tx, 19, tw,
                               fill=85 if paused else 175)

        if paused:
            self._render_paused(canvas, draw, tx, w, h, st)
            return

        # --- double horizontal level bars (now-playing AND spectrum, no switch) ---
        if self.app.spectrum_available():
            self._render_level_bars(canvas, draw, tx, w, h)

        # --- meta line: play glyph + samplerate/bitdepth ... volume ---
        meta_f = self.app.fonts.get("sans", 9)
        iy = 33
        icon = self.app.icons.get("play")
        if icon is not None:
            canvas.paste(icon.convert("L"), (tx, iy))
            mx = tx + icon.width + 4
        else:
            mx = tx
        meta = "  ".join(x for x in (st.samplerate, st.bitdepth) if x)
        self.text(canvas, (mx, iy + 1), meta, meta_f, fill=135)
        vol = "Vol %d" % st.volume
        vw = self.text_width(draw, vol, meta_f)
        self.text(canvas, (w - vw - 2, iy + 1), vol, meta_f, fill=135)

        # --- refined progress + remaining time ---
        self._render_progress(canvas, draw, tx, w, h, st)

    # --- helpers ---
    @staticmethod
    def _draw_vinyl(draw, cx, cy, R, shades):
        """A vinyl record (disc + grooves + label + spindle hole) -- the no-art
        default. shades = (rim, body, groove, label)."""
        rim, body, groove, label = shades
        draw.ellipse((cx - R, cy - R, cx + R, cy + R), fill=body, outline=rim)
        step = max(3, R // 4)
        for gr in range(R - 4, 9, -step):
            draw.ellipse((cx - gr, cy - gr, cx + gr, cy + gr), outline=groove)
        lr = max(5, R // 4)
        draw.ellipse((cx - lr, cy - lr, cx + lr, cy + lr), fill=label)
        draw.ellipse((cx - 2, cy - 2, cx + 2, cy + 2), fill=10)

    def _draw_art_placeholder(self, canvas, draw, aw, h, dim=False):
        """Panel no-art default: a vinyl record filling the art square (some web
        radio gives no cover) -- nicer than a bare note."""
        draw.rectangle((0, 0, aw - 1, h - 1), fill=12)
        shades = (40, 22, 32, 70) if dim else (95, 40, 60, 120)
        self._draw_vinyl(draw, aw // 2, h // 2, min(aw, h) // 2 - 2, shades)

    def _title_lines(self, st):
        """Resolve the two text lines. Normal tracks: title + artist. Radio often
        gives only a title like 'STATION - tagline' with no artist/album, so when
        the artist is empty and the title has a ' - ', split it into a bold first
        line + a grey second line -- filling the otherwise empty row."""
        title = (st.title or "(no title)").strip()
        sub = (st.artist or st.album or "").strip()
        if not sub and " - " in title:
            head, tail = title.split(" - ", 1)
            return head.strip(), tail.strip()
        return title, sub

    def _bleed(self, canvas, art, aw, h):
        """Fade the art's right edge into black so it dissolves into the text
        field instead of hard-cutting."""
        strip = Image.new("L", (self.FADE, h), 0)
        sp = strip.load()
        ap = art.load()
        for y in range(h):
            edge = ap[aw - 1, y]
            for x in range(self.FADE):
                sp[x, y] = int(edge * (1 - (x + 1) / (self.FADE + 1)))
        canvas.paste(strip, (aw, 0))

    def _floor_values(self):
        if self._test_floor is not None:
            return self._smoother.update(self._test_floor)
        if self._reader is None:
            self._reader = FifoBars(DISPLAY_FIFO, self.FLOOR_BARS, log=self.app.log)
        return self._smoother.update(self._reader.read())

    def _render_level_bars(self, canvas, draw, tx, w, h):
        """Two thin horizontal level bars in the bottom strip -- low-band and
        high-band energy of the live spectrum -- each pulsing OUT from the centre
        with a bright-centre gradient. Replaces the old vertical spectrum floor:
        same data, a calmer/cleaner 'stereo VU' feel."""
        vals = self._floor_values()
        n = len(vals)
        if n == 0:
            return
        half = max(1, n // 2)
        lo = sum(vals[:half]) / half
        hi = sum(vals[half:]) / max(1, n - half)
        x0, x1 = tx, w - 3
        cx = (x0 + x1) // 2
        halfw = max(1, (x1 - x0) // 2)
        px = canvas.load()
        for i, lvl in enumerate((lo, hi)):
            by = 49 + i * 5                                # two 2px bars, y49 + y54
            draw.line((x0, by, x1, by), fill=32)          # dim full-width track
            draw.line((x0, by + 1, x1, by + 1), fill=20)
            ext = int(halfw * max(0.0, min(1.0, lvl)))
            for dx in range(ext + 1):
                g = max(80, int(248 - 150 * dx / halfw))  # bright centre -> dim tips
                px[cx - dx, by] = g
                px[cx + dx, by] = g
                px[cx - dx, by + 1] = max(60, g - 50)      # dimmer 2nd row -> depth
                px[cx + dx, by + 1] = max(60, g - 50)

    def _render_progress(self, canvas, draw, tx, w, h, st):
        # A QUIET position line along the very bottom edge -- the panel's "floor",
        # not a third competing bar above the level meters. A live stream has no
        # position, so it shows a LIVE badge instead of a (meaningless) timer.
        f = self.app.fonts.get("sans", 8)
        py = h - 1
        if bool(st.stream) or st.duration_s <= 0:
            label = "LIVE"
            tw = self.text_width(draw, label, f)
            draw.ellipse((w - tw - 12, py - 6, w - tw - 8, py - 2), fill=210)  # dot
            self.text(canvas, (w - 2, h - 9), label, f, fill=175, anchor="ra")
            draw.line((tx, py, w - tw - 16, py), fill=24)                       # baseline
            return
        label = "-" + _mmss(st.duration_s - self.app.store.live_position_ms() / 1000.0)
        tw = self.text_width(draw, label, f)
        self.text(canvas, (w - 2, h - 9), label, f, fill=130, anchor="ra")
        right = w - tw - 6
        draw.line((tx, py, right, py), fill=24)
        fillw = int(max(0, right - tx) * self.app.store.progress_fraction())
        if fillw > 0:
            draw.line((tx, py, tx + fillw, py), fill=130)

    def _render_paused(self, canvas, draw, tx, w, h, st):
        py = 39
        draw.rectangle((tx, py, tx + 4, py + 15), fill=170)
        draw.rectangle((tx + 8, py, tx + 12, py + 15), fill=170)
        self.text(canvas, (tx + 22, py + 3), "PAUSED",
                  self.app.fonts.get("sans", 11), fill=115)
        # frozen progress hairline so the listener keeps their place
        if st.duration_s > 0:
            pyb = h - 3
            right = w - 3
            draw.line((tx, pyb, right, pyb), fill=40)
            fillw = int((right - tx) * self.app.store.progress_fraction())
            if fillw > 0:
                draw.line((tx, pyb, tx + fillw, pyb), fill=115)

    # --- Cinema theme: full-bleed art + bottom scrim + overlaid type ----------
    def _get_scrim(self, w, h):
        """Cached bottom-up darkening gradient (transparent at top, ~dark at the
        bottom) so title/artist read over bright album art. Static -> built once."""
        if self._scrim is not None and self._scrim.size == (w, h):
            return self._scrim
        scrim = Image.new("L", (w, h), 0)
        px = scrim.load()
        knee = h * 0.35
        for y in range(h):
            t = max(0.0, (y - knee) / max(1.0, h - knee))
            v = int(215 * min(1.0, t))
            for x in range(w):
                px[x, y] = v
        self._scrim = scrim
        return scrim

    def _render_cinema(self, canvas, draw, w, h, st, paused):
        art = self.app.albumart_cinema.get(st.albumart)
        if art is not None:
            a = art.convert("L")
            if paused:
                a = a.point(lambda p: int(p * 0.55))
            canvas.paste(a, (0, 0))
            # darken the lower band so text reads (composite black through scrim)
            canvas.paste(Image.composite(Image.new("L", (w, h), 0), canvas.copy(),
                                         self._get_scrim(w, h)), (0, 0))
        else:
            # no cover (some web radio): a large, dim vinyl on the right instead of
            # flat black -- reads as intentional, leaves the left clear for text.
            self._draw_vinyl(draw, w - 30, h // 2, 42, (62, 16, 34, 60))
        # title + artist over the lower third (split a "STATION - tagline" radio
        # title when there is no artist, like the Panel theme)
        line1, line2 = self._title_lines(st)
        self.draw_text_clipped(canvas, "cine_title", line1,
                               self.app.fonts.get("sans_bold", 16), 6, 28, w - 12,
                               fill=140 if paused else 255)
        self.draw_text_clipped(canvas, "cine_artist", line2,
                               self.app.fonts.get("sans", 10), 6, 49, w - 12,
                               fill=110 if paused else 205)
        if paused:
            draw.rectangle((6, 4, 9, 14), fill=185)
            draw.rectangle((13, 4, 16, 14), fill=185)
        # minimal progress hairline along the very bottom
        fillw = int((w - 1) * self.app.store.progress_fraction())
        if fillw > 0:
            draw.line((0, h - 1, fillw, h - 1), fill=255)
