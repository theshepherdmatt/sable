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


def _playables(items):
    """The playable leaves (songs/tracks) in a list -- not folders, not the
    synthetic Play-all row, and with a uri to play."""
    return [it for it in items
            if not it.get("_play_all") and not _is_folder(it) and it.get("uri")]


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
        self._open = None          # (uri, title) when entered from the home carousel
        self._replace_top = False

    def open_source(self, uri, title):
        """Enter the browser at a Volumio source uri (set by the home carousel
        before app.go('browse'))."""
        self._open = (uri, title)

    @staticmethod
    def _frame(title, items):
        return {"title": title, "items": items, "index": 0}

    @property
    def _cur(self):
        return self.stack[-1]

    def on_enter(self, **kwargs):
        op = self._open
        self._open = None
        if op and op[0]:
            uri, title = op
            self.stack = [self._frame(title, [])]
            self._pending_title = title
            self._replace_top = True       # the first result IS the root frame
            self.loading = True
            if self.app.listener is not None:
                self.app.listener.browse(uri)
        else:
            self.stack = [self._frame("Music", list(_ROOTS))]
            self.loading = False
            self._replace_top = False

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
        if item.get("_play_all"):
            songs = _playables(f["items"])
            if songs and self.app.listener is not None:
                self.app.listener.play_all(songs)
            self.app.go(self.app.nowplaying_screen())
        elif _is_folder(item):
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
            self.app.go("home")          # back to the sources carousel

    # --- async browse result (called on the listener thread) ---
    def on_browse_data(self, data):
        if not self.loading:
            return
        items, _prev = _items_from_response(data)
        if len(_playables(items)) >= 2:           # an album/list -> offer Play all
            items = [{"title": "Play all", "_play_all": True}] + items
        frame = self._frame(getattr(self, "_pending_title", "..."), items)
        if self._replace_top:
            self.stack[-1] = frame       # carousel entry: result becomes the root
            self._replace_top = False
        else:
            self.stack.append(frame)     # drilling deeper
        self.loading = False
        self.app.render()

    # --- render ---
    def render(self, canvas, draw, w, h):
        f = self._cur
        if self.loading:
            self.text(canvas, (4, 1), (f["title"] or "").upper(),
                      self.app.fonts.get("sans_bold", 10), fill=120)
            self.text(canvas, (8, self.TOP + self.ROW_H), "Loading...",
                      self.app.fonts.get("sans", 12), fill=130)
            return
        if not f["items"]:
            self.text(canvas, (4, 1), (f["title"] or "").upper(),
                      self.app.fonts.get("sans_bold", 10), fill=120)
            self.text(canvas, (8, self.TOP + self.ROW_H - 2), "Empty",
                      self.app.fonts.get("sans", 12), fill=150)
            self.text(canvas, (8, self.TOP + self.ROW_H + 14), "hold to go back",
                      self.app.fonts.get("sans", 9), fill=90)
            return
        rows = []
        for it in f["items"]:
            label = it.get("title") or it.get("name") or "?"
            rows.append((label, ">" if _is_folder(it) else None))
        self.draw_menu_surface(canvas, draw, w, h, f["title"], rows, f["index"],
                               key_prefix="br", top=self.TOP, row_h=self.ROW_H,
                               nrows=self.ROWS)
