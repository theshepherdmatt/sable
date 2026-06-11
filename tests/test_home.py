"""Home / sources carousel. Run: python3 tests/test_home.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PIL import Image, ImageDraw

from sable.app import build_sim_app


def _app_home():
    app = build_sim_app(frames_dir=None)
    app.display.ascii_preview = False
    h = app.fsm.screens["home"]
    h.items = [{"name": n} for n in
               ["Music Library", "Favourites", "Playlists", "Web Radio", "Tidal"]]
    h.items.append({"name": "Settings", "_settings": True})
    return app, h


def test_build_appends_settings():
    app = build_sim_app(frames_dir=None)
    items = app.fsm.screens["home"]._build()   # no listener -> just Settings
    assert items and items[-1].get("_settings")


def test_scroll_clamps_and_moves():
    _app, h = _app_home()
    h.index = 0
    h.handle_scroll(-1)
    assert h.index == 0                        # clamp at start
    h.handle_scroll(1)
    assert h.index == 1
    h.handle_scroll(99)
    assert h.index == len(h.items) - 1         # clamp at end


def test_select_settings_goes_to_menu():
    app, h = _app_home()
    h.index = len(h.items) - 1                  # Settings
    h.handle_select()
    assert app.fsm.current.name == "menu"


def test_select_source_opens_browse():
    app, h = _app_home()
    h.items[2]["uri"] = "playlists"
    h.index = 2
    h.handle_select()
    assert app.fsm.current.name == "browse"


def test_renders_nonblank():
    app, h = _app_home()
    h.index = 2
    img = Image.new("L", (256, 64), 0)
    h.render(img, ImageDraw.Draw(img), 256, 64)
    assert img.getbbox() is not None


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print("\nAll %d home tests passed." % len(tests))


if __name__ == "__main__":
    main()
