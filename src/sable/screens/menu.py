"""A data-driven, nestable list menu.

ONE class drives the whole menu tree: items are (label, target) where target is
either a submenu (a list of items), a callable action, or the "__back__" sentinel.
Navigation is an internal frame stack -- the FSM only knows clock<->menu, so
adding submenus needs no new FSM states (the old code reimplemented a screen per
menu). Scroll clamps at the ends (no wrap); long-press pops one level, or exits to
the clock at the root.
"""
from .base import Screen

_BACK = "__back__"


class MenuScreen(Screen):
    name = "menu"
    ROWS = 3
    ROW_H = 16
    TOP = 16

    def __init__(self, app):
        super().__init__(app)
        self.root = self._build_tree()
        self.stack = [self._frame("MENU", self.root)]

    def _build_tree(self):
        return [
            ("Music", self._open_browse),
            ("Now Playing", self._now_playing),
            ("Display Mode", [
                ("Modern", lambda: self._set_screen("modern")),
                ("Spectrum Bars", lambda: self._set_spectrum("bars")),
                ("Spectrum Dots", lambda: self._set_spectrum("dots")),
                ("Back", _BACK),
            ]),
            ("Brightness", [
                ("Low", lambda: self._set_brightness("low")),
                ("Medium", lambda: self._set_brightness("medium")),
                ("High", lambda: self._set_brightness("high")),
                ("Back", _BACK),
            ]),
        ]

    @staticmethod
    def _frame(label, items):
        return {"label": label, "items": items, "index": 0}

    @property
    def _cur(self):
        return self.stack[-1]

    # --- navigation ---
    def on_enter(self, **kwargs):
        self.stack = [self._frame("MENU", self.root)]
        self.app.fsm.reset_menu_timer()

    def handle_scroll(self, delta):
        f = self._cur
        f["index"] = max(0, min(f["index"] + delta, len(f["items"]) - 1))
        self.app.fsm.reset_menu_timer()

    def handle_select(self):
        self.app.fsm.reset_menu_timer()
        f = self._cur
        _label, target = f["items"][f["index"]]
        if target == _BACK:
            self._pop()
        elif isinstance(target, list):
            self.stack.append(self._frame(_label, target))
        elif callable(target):
            target()

    def handle_back(self):
        # long-press: up one level, or leave the menu at the root.
        self._pop()

    def _pop(self):
        if len(self.stack) > 1:
            self.stack.pop()
            self.app.fsm.reset_menu_timer()
        else:
            # Leaving the menu: return to the state-appropriate base screen
            # (now-playing while active, clock when stopped) -- no clock flash.
            self.app.go(self.app.base_screen())

    # --- actions ---
    def _open_browse(self):
        self.app.go("browse")

    def _now_playing(self):
        self.app.go(self.app.nowplaying_screen())

    def _set_screen(self, name):
        self.app.settings.set("display", "screen", name)
        self.app.go(self.app.nowplaying_screen())

    def _set_spectrum(self, style):
        self.app.settings.set("display", "screen", "spectrum")
        self.app.settings.set("display", "spectrum_style", style)
        self.app.go(self.app.nowplaying_screen())

    def _set_brightness(self, level):
        # Applied live so the user can compare; menu stays open. Routed through
        # the app so it becomes the idle ladder's base (dim/dark restore to it).
        self.app.settings.set("display", "brightness", level)
        self.app.set_brightness_from_settings()

    # --- render ---
    def render(self, canvas, draw, w, h):
        f = self._cur
        items, index = f["items"], f["index"]
        self.text(canvas, (4, 1), f["label"], self.app.fonts.get("sans_bold", 12), fill=255)
        item_font = self.app.fonts.get("sans", 12)
        start = max(0, min(index - 1, len(items) - self.ROWS))
        for i in range(start, min(start + self.ROWS, len(items))):
            y = self.TOP + (i - start) * self.ROW_H
            selected = i == index
            if selected:
                draw.rectangle((0, y - 1, w - 1, y + self.ROW_H - 3), fill=255)
            self.text(canvas, (6, y), items[i][0], item_font, fill=0 if selected else 255)
