"""Home / sources carousel -- the top-level navigation.

A horizontal strip of icons built DYNAMICALLY from Volumio's browse sources
(listener.browse_sources) plus a Settings entry. The selected icon is centred and
bright; neighbours dim toward the edges (greyscale depth). Turning the rotary slides
the strip with an eased animation (the tick loop re-renders, so render() decays the
offset). Select drills a source into BrowseScreen or opens the settings menu;
long-press / back returns to now-playing.
"""
import os

from PIL import Image

from .base import Screen

_ICON_DIR = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "assets", "menu"))

# Volumio source name (lowercased) -> menu-icon file. Unknown -> a sensible default.
_NAME_ICONS = {
    "music library": "music_library", "musiclibrary": "music_library",
    "library": "music_library",
    "favourites": "favorites", "favorites": "favorites",
    "playlists": "playlists",
    "artists": "artists", "albums": "albums", "genres": "genres",
    "web radio": "web_radio", "webradio": "web_radio", "radio": "web_radio",
    "media servers": "media_servers", "media server": "media_servers",
    "last 100": "last_100", "last_100": "last_100",
    "nas": "nas", "usb": "usb",
    "tidal": "tidal", "qobuz": "qobuz", "spotify": "spotify",
    "radio paradise": "radio_paradise",
    "settings": "config", "config": "config",
}


def _icon_name(label):
    return _NAME_ICONS.get((label or "").strip().lower(), "music_library")


class HomeScreen(Screen):
    name = "home"
    SEL = 44                       # selected icon px
    NEIGHBOUR = 36                 # off-centre icon px
    SPACING = 56                   # centre-to-centre px
    CY = 25                        # icon-row centre y

    def __init__(self, app):
        super().__init__(app)
        self._cache = {}           # (name,size) -> L image | None
        self.items = []
        self.index = 0
        self._slide = 0.0          # animated px offset, eases to 0

    # --- data ---
    def _build(self):
        srcs = getattr(self.app.listener, "browse_sources", None) if self.app.listener else None
        items = [s for s in (srcs or []) if isinstance(s, dict)
                 and (s.get("name") or s.get("title"))]
        items.append({"name": "Settings", "_settings": True})
        return items

    def on_enter(self, **kwargs):
        self.items = self._build()
        self.index = max(0, min(self.index, len(self.items) - 1))
        self._slide = 0.0
        self.app.fsm.reset_menu_timer()
        if self.app.listener is not None:
            self.app.listener.get_sources()      # refresh (async)

    def refresh_sources(self, _data=None):
        """pushBrowseSources arrived (listener thread) -> rebuild, keep the cursor
        on the same-named item, and redraw if we are showing."""
        keep = self.items[self.index].get("name") if self.items else None
        self.items = self._build()
        if keep:
            for i, it in enumerate(self.items):
                if it.get("name") == keep:
                    self.index = i
                    break
        self.index = max(0, min(self.index, len(self.items) - 1))
        if self.app.fsm.current is self:
            self.app.render()

    # --- input ---
    def handle_scroll(self, delta):
        if not self.items:
            return
        new = max(0, min(self.index + int(delta), len(self.items) - 1))
        if new != self.index:
            self._slide += (new - self.index) * self.SPACING   # jump, then ease back
            self.index = new
        self.app.fsm.reset_menu_timer()

    def handle_select(self):
        self.app.fsm.reset_menu_timer()
        if not self.items:
            return
        it = self.items[self.index]
        if it.get("_settings"):
            self.app.go("menu")
            return
        browse = self.app.fsm.screens.get("browse")
        if browse is not None:
            browse.open_source(it.get("uri", ""),
                               it.get("name") or it.get("title") or "Browse")
            self.app.go("browse")

    def handle_back(self):
        self.app.go(self.app.base_screen())

    # --- render (called every tick -> eases the slide) ---
    def _icon(self, name, size):
        k = (name, size)
        if k not in self._cache:
            try:
                im = Image.open(os.path.join(_ICON_DIR, name + ".png"))
                self._cache[k] = im.convert("L").resize((size, size), Image.LANCZOS)
            except Exception:
                self._cache[k] = None
        return self._cache[k]

    def render(self, canvas, draw, w, h):
        if abs(self._slide) > 0.5:                 # ease the slide toward 0
            self._slide *= 0.55
            if abs(self._slide) <= 0.5:
                self._slide = 0.0
        if not self.items:
            self.text(canvas, (8, 22), "Loading sources...",
                      self.app.fonts.get("sans", 11), fill=130)
            return
        cx = w // 2
        # draw far -> near so the selected icon sits on top
        order = sorted(range(len(self.items)), key=lambda i: abs(i - self.index),
                       reverse=True)
        for i in order:
            d = i - self.index
            x = cx + int(round(d * self.SPACING - self._slide))
            if x < -40 or x > w + 40:
                continue
            sel = (d == 0)
            size = self.SEL if sel else self.NEIGHBOUR
            dim = 1.0 if sel else (0.6 if abs(d) == 1 else 0.3)
            label = self.items[i].get("name") or self.items[i].get("title") or ""
            ic = self._icon(_icon_name(label), size)
            iy = self.CY - size // 2
            if ic is not None:
                g = ic if sel else ic.point(lambda p: int(p * dim))
                canvas.paste(g, (x - size // 2, iy))
            else:
                c = int(150 * dim)
                draw.rounded_rectangle((x - size // 2, iy, x - size // 2 + size,
                                        iy + size), radius=4, outline=c)
        # selected label, centred
        label = self.items[self.index].get("name") or self.items[self.index].get("title") or ""
        f = self.app.fonts.get("sans_bold", 12)
        tw = self.text_width(draw, label, f)
        self.text(canvas, (cx - tw // 2, 50), label, f, fill=255)
        # scroll affordances
        if self.index > 0:
            draw.polygon([(2, self.CY), (8, self.CY - 5), (8, self.CY + 5)], fill=85)
        if self.index < len(self.items) - 1:
            draw.polygon([(w - 2, self.CY), (w - 8, self.CY - 5), (w - 8, self.CY + 5)],
                         fill=85)
