"""The idle clock -- shown when nothing is playing.

Elevated, not just a time string: a large time, the date beneath it, and a dim
"last played" ghost line (artist - title) so the panel keeps its identity while
idle instead of looking switched-off. Drawn crisp via Screen.text(); the idle
ladder (App) handles dimming + burn-in pixel-shift around this, so the clock
itself just draws at full values.
"""
import time

from .base import Screen


class ClockScreen(Screen):
    name = "clock"

    def handle_select(self):
        self.app.fsm.dispatch("menu")

    def render(self, canvas, draw, w, h):
        st = self.app.settings
        show_sec = st.get("clock", "show_seconds", default=False)
        now = time.localtime()
        text = time.strftime("%H:%M:%S" if show_sec else "%H:%M", now)

        # Dim "last played" ghost: identity while idle. Only when a track is known.
        track = self.app.store.get()
        ghost = (track.title or "").strip()
        if ghost:
            line = (track.artist + "  -  " + track.title) if track.artist else track.title
            self.draw_text_clipped(canvas, "clk_ghost", line.upper(),
                                   self.app.fonts.get("sans", 9), 4, 0, w - 8, fill=70)
            ty = h // 2 + 5
        else:
            ty = h // 2 - 1

        face = self.app.fonts.get("sans_bold", 34 if show_sec else 38)
        self.text(canvas, (w // 2, ty), text, face, fill=255, anchor="mm")

        date = time.strftime("%a %d %b", now).upper()
        self.text(canvas, (w // 2, h - 7), date,
                  self.app.fonts.get("sans", 10), fill=110, anchor="mm")
