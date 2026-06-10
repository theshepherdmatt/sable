"""Boot splash, shown immediately and held through the Volumio + NTP gates."""
from .base import Screen


class SplashScreen(Screen):
    name = "splash"

    def __init__(self, app):
        super().__init__(app)
        self.subtitle = "starting"

    def render(self, canvas, draw, w, h):
        title = self.app.fonts.get("sans_bold", 28)
        draw.text((w // 2, h // 2 - 8), "SABLE", font=title, fill=255, anchor="mm")
        sub = self.app.fonts.get("sans", 11)
        draw.text((w // 2, h - 12), self.subtitle, font=sub, fill=255, anchor="mm")
