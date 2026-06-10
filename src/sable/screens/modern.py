"""The "modern" now-playing screen.

Layout (256x64): album art (or placeholder) on the left; scrolling title + artist
on the right; a status glyph, samplerate/bitdepth and volume on the meta line; a
live progress bar along the bottom. Everything is read from StateStore.get(); the
seek bar uses StateStore.live_position_ms() so it advances on the render tick
without this screen owning a thread. Scrolling comes from the Screen base.
"""
from .base import Screen


class ModernScreen(Screen):
    name = "modern"
    ART = 56

    def handle_select(self):
        self.app.fsm.dispatch("menu")

    def handle_back(self):
        self.app.fsm.dispatch("back")

    def render(self, canvas, draw, w, h):
        st = self.app.store.get()
        ax, ay, box = 2, 4, self.ART

        # --- album art (fetched off the tick; placeholder until/if it arrives) ---
        art = self.app.albumart.get(st.albumart)
        if art is not None:
            canvas.paste(art.convert(canvas.mode), (ax, ay))
        else:
            draw.rectangle((ax, ay, ax + box - 1, ay + box - 1), outline=255)
            draw.line((ax, ay, ax + box - 1, ay + box - 1), fill=255)
            draw.line((ax + box - 1, ay, ax, ay + box - 1), fill=255)

        tx = ax + box + 6
        tw = w - tx - 2

        # --- title / artist (scroll if wider than the box) ---
        self.draw_text_clipped(canvas, "title", st.title or "(no title)",
                               self.app.fonts.get("sans_bold", 14), tx, 1, tw)
        self.draw_text_clipped(canvas, "artist", st.artist or "",
                               self.app.fonts.get("sans", 11), tx, 18, tw)

        # --- meta line: status glyph + samplerate/bitdepth ... volume ---
        meta_f = self.app.fonts.get("sans", 9)
        iy = 33
        icon = self.app.icons.get("play" if st.status == "play" else "pause")
        if icon is not None:
            canvas.paste(icon.convert(canvas.mode), (tx, iy))
            mx = tx + icon.width + 4
        else:
            mx = tx
        meta = "  ".join(x for x in (st.samplerate, st.bitdepth) if x)
        draw.text((mx, iy + 1), meta, font=meta_f, fill=255)
        vol = "Vol %d" % st.volume
        vw = self.text_width(draw, vol, meta_f)
        draw.text((w - vw - 2, iy + 1), vol, font=meta_f, fill=255)

        # --- progress bar (live position) ---
        py = h - 6
        draw.rectangle((tx, py, w - 3, py + 3), outline=255)
        inner = (w - 3) - tx - 2
        fillw = int(inner * self.app.store.progress_fraction())
        if fillw > 0:
            draw.rectangle((tx + 1, py + 1, tx + 1 + fillw, py + 2), fill=255)
