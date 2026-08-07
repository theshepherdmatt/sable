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
            ("Now Playing", self._now_playing),
            ("Display Mode", [
                ("Modern: Panel", lambda: self._set_modern("panel")),
                ("Modern: Cinema", lambda: self._set_modern("cinema")),
                ("Spectrum Bars", lambda: self._set_spectrum("bars")),
                ("Spectrum Dots", lambda: self._set_spectrum("dots")),
                ("Spectrum Mirror", lambda: self._set_spectrum("mirror")),
                ("Spectrum Ribbon", lambda: self._set_spectrum("ribbon")),
                ("VU Meter", lambda: self._set_spectrum("vu")),
                ("Back", _BACK),
            ], self._display_mode_label),
            ("Brightness", [
                ("Low", lambda: self._set_brightness("low")),
                ("Medium", lambda: self._set_brightness("medium")),
                ("High", lambda: self._set_brightness("high")),
                ("Back", _BACK),
            ], lambda: self.app.settings.get(
                "display", "brightness", default="medium").capitalize()),
            ("Screen Rotation", [
                ("Normal", lambda: self._set_rotate(0)),
                ("Upside-down", lambda: self._set_rotate(180)),
                ("Back", _BACK),
            ], self._rotate_label),
        ]

    def _rotate_label(self):
        deg = self.app.settings.get("display", "rotate", default=0)
        try:
            deg = int(deg or 0)
        except (TypeError, ValueError):
            deg = 0
        return "180deg" if deg == 180 else "Normal"

    def _display_mode_label(self):
        screen = self.app.settings.get("display", "screen", default="modern")
        if screen != "spectrum":
            return self.app.settings.get(
                "display", "theme", default="panel").capitalize()
        style = self.app.settings.get("display", "spectrum_style", default="bars")
        return "VU" if style == "vu" else style.capitalize()

    @staticmethod
    def _unpack(item):
        """Items are (label, target) or (label, target, value_fn)."""
        if len(item) == 3:
            return item[0], item[1], item[2]
        return item[0], item[1], None

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
        _label, target, _vfn = self._unpack(f["items"][f["index"]])
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

    def _set_modern(self, theme):
        self.app.settings.set("display", "screen", "modern")
        self.app.settings.set("display", "theme", theme)
        self.app.go(self.app.nowplaying_screen())

    def _set_spectrum(self, style):
        self.app.settings.set("display", "screen", "spectrum")
        self.app.settings.set("display", "spectrum_style", style)
        self.app.go(self.app.nowplaying_screen())

    def _set_rotate(self, degrees):
        # Applied live (the panel re-inits) so the user sees the result while
        # still in the menu -- the point of rotating is that the panel is
        # mounted upside-down, and making them restart to find out defeats it.
        # The menu stays open, exactly like Brightness.
        self.app.settings.set("display", "rotate", int(degrees))
        self.app.set_rotate_from_settings()

    def _set_brightness(self, level):
        # Applied live so the user can compare; menu stays open. Routed through
        # the app so it becomes the idle ladder's base (dim/dark restore to it).
        self.app.settings.set("display", "brightness", level)
        self.app.set_brightness_from_settings()

    # --- render ---
    def _row_value(self, label, target, vfn):
        if vfn is not None:
            return vfn()         # a live value (e.g. Display Mode -> "Mirror")
        if isinstance(target, list):
            return ">"           # a submenu (has children)
        return None              # a leaf action / back -> no affordance

    def render(self, canvas, draw, w, h):
        f = self._cur
        rows = []
        for item in f["items"]:
            label, target, vfn = self._unpack(item)
            rows.append((label, self._row_value(label, target, vfn)))
        self.draw_menu_surface(canvas, draw, w, h, f["label"], rows, f["index"],
                               key_prefix="menu", top=self.TOP, row_h=self.ROW_H,
                               nrows=self.ROWS)
