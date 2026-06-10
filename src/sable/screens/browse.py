"""On-device music browser (focused: Favourites / Playlists / Radio).

A drill-down list backed by Volumio's browseLibrary. Synthetic roots open a
Volumio uri; folders drill in (browse the item's uri); playable leaves start
playback (listener.play_item -> replaceAndPlay) and jump to the now-playing
screen. Browse responses arrive asynchronously on the listener thread via
app.listener.on_browse, which we route to on_browse_data().

Navigation is an internal frame stack (like MenuScreen): each frame holds the
items of one level + the cursor. Scroll clamps; LEFT/long-press pops a level or
exits to the menu at the root.
"""
from .base import Screen

# Synthetic top level -- the focused set the user asked for.
_ROOTS = [
    {"title": "Favourites", "uri": "favourites", "_folder": True},
    {"title": "Playlists", "uri": "playlists", "_folder": True},
    {"title": "Radio", "uri": "radio", "_folder": True},
]

# Volumio item types that are containers to drill into (everything else plays).
_FOLDER_TYPES = {"folder", "folders", "internal-folder", "streaming-category",
                 "playlists-category", "remdisk", "cuesong-folder"}


def _is_folder(item):
    if item.get("_folder"):
        return True
    t = (item.get("type") or "").lower()
    return t.endswith("category") or t in _FOLDER_TYPES


def _items_from_response(data):
    """Flatten navigation.lists[].items[] into one list; return (items, prev_uri)."""
    nav = (data or {}).get("navigation", {}) if isinstance(data, dict) else {}
    items = []
    for lst in nav.get("lists", []) or []:
        if isinstance(lst, dict):
            items.extend([it for it in lst.get("items", []) or [] if isinstance(it, dict)])
    prev = (nav.get("prev") or {}).get("uri") if isinstance(nav.get("prev"), dict) else None
    return items, prev


class BrowseScreen(Screen):
    name = "browse"
    ROWS = 3
    ROW_H = 16
    TOP = 16

    def __init__(self, app):
        super().__init__(app)
        self.stack = [self._frame("Music", list(_ROOTS))]
        self.loading = False

    @staticmethod
    def _frame(title, items):
        return {"title": title, "items": items, "index": 0}

    @property
    def _cur(self):
        return self.stack[-1]

    def on_enter(self, **kwargs):
        self.stack = [self._frame("Music", list(_ROOTS))]
        self.loading = False

    # --- input ---
    def handle_scroll(self, delta):
        if self.loading:
            return
        f = self._cur
        f["index"] = max(0, min(f["index"] + delta, max(0, len(f["items"]) - 1)))

    def handle_select(self):
        if self.loading:
            return
        f = self._cur
        if not f["items"]:
            return
        item = f["items"][f["index"]]
        if _is_folder(item):
            if self.app.listener is None:
                return
            self._pending_title = item.get("title") or "..."
            self.loading = True
            self.app.listener.browse(item.get("uri", ""))
        else:
            if self.app.listener is not None:
                self.app.listener.play_item(item)
            self.app.go(self.app.nowplaying_screen())

    def handle_back(self):
        if len(self.stack) > 1:
            self.stack.pop()
            self.loading = False
        else:
            self.app.go("menu")

    # --- async browse result (called on the listener thread) ---
    def on_browse_data(self, data):
        if not self.loading:
            return
        items, _prev = _items_from_response(data)
        self.stack.append(self._frame(getattr(self, "_pending_title", "..."), items))
        self.loading = False
        self.app.render()

    # --- render ---
    def render(self, canvas, draw, w, h):
        f = self._cur
        draw.text((4, 1), f["title"], font=self.app.fonts.get("sans_bold", 12), fill=255)
        item_font = self.app.fonts.get("sans", 12)
        if self.loading:
            draw.text((6, self.TOP + self.ROW_H), "Loading...", font=item_font, fill=255)
            return
        items, index = f["items"], f["index"]
        if not items:
            draw.text((6, self.TOP + self.ROW_H), "(empty)", font=item_font, fill=255)
            return
        start = max(0, min(index - 1, len(items) - self.ROWS))
        for i in range(start, min(start + self.ROWS, len(items))):
            y = self.TOP + (i - start) * self.ROW_H
            selected = i == index
            if selected:
                draw.rectangle((0, y - 1, w - 1, y + self.ROW_H - 3), fill=255)
            label = items[i].get("title") or items[i].get("name") or "?"
            self.draw_text_clipped(canvas, "br_%d" % i, label, item_font,
                                   6, y, w - 8, fill=0 if selected else 255)
