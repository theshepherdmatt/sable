"""Default screen: the clock. Shown when nothing is playing."""
import time

from .base import Screen


class ClockScreen(Screen):
    name = "clock"

    def handle_select(self):
        self.app.fsm.dispatch("menu")

    def render(self, canvas, draw, w, h):
        st = self.app.settings
        show_sec = st.get("clock", "show_seconds", default=False)
        show_date = st.get("clock", "show_date", default=False)
        now = time.localtime()
        text = time.strftime("%H:%M:%S" if show_sec else "%H:%M", now)
        face = self.app.fonts.get("sans_bold", 32 if show_sec else 40)
        y = h // 2 - 8 if show_date else h // 2
        draw.text((w // 2, y), text, font=face, fill=255, anchor="mm")
        if show_date:
            date = time.strftime("%a %d %b", now)
            draw.text((w // 2, h - 10), date,
                      font=self.app.fonts.get("sans", 12), fill=255, anchor="mm")
