"""Slice 5 self-tests: menu item model + designed-surface render.

Run: python3 tests/test_menu.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PIL import Image, ImageDraw

from sable.app import build_sim_app
from sable.screens.menu import MenuScreen


def _app():
    app = build_sim_app(frames_dir=None)
    app.display.ascii_preview = False
    return app


def test_unpack_two_and_three_tuples():
    assert MenuScreen._unpack(("L", "t")) == ("L", "t", None)
    f = lambda: "v"
    assert MenuScreen._unpack(("L", "t", f)) == ("L", "t", f)


def test_display_mode_label_reflects_settings():
    app = _app()
    m = app.fsm.screens["menu"]
    d = app.settings._data.setdefault("display", {})
    d["screen"] = "modern"
    d["theme"] = "panel"
    assert m._display_mode_label() == "Panel"
    d["theme"] = "cinema"
    assert m._display_mode_label() == "Cinema"
    d["screen"] = "spectrum"
    d["spectrum_style"] = "mirror"
    assert m._display_mode_label() == "Mirror"


def test_row_value_chevron_vs_value_vs_none():
    app = _app()
    m = app.fsm.screens["menu"]
    assert m._row_value("X", lambda: None, None) is None     # leaf action -> none
    assert m._row_value("X", [], None) == ">"                # submenu -> chevron
    assert m._row_value("X", "__back__", None) is None       # back -> nothing
    assert m._row_value("X", [], lambda: "High") == "High"   # value_fn wins


def test_menu_root_renders_nonblank():
    app = _app()
    app.go("menu")
    img = Image.new("L", (256, 64), 0)
    app.fsm.screens["menu"].render(img, ImageDraw.Draw(img), 256, 64)
    assert img.getbbox() is not None


def test_select_drills_into_submenu():
    app = _app()
    app.go("menu")
    m = app.fsm.screens["menu"]
    m._cur["index"] = 2                  # Display Mode (a submenu)
    m.handle_select()
    assert len(m.stack) == 2
    assert m._cur["label"] == "Display Mode"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print("\nAll %d menu tests passed." % len(tests))


if __name__ == "__main__":
    main()
