"""Slice 7 self-tests: screen-to-screen crossfade. Run directly."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PIL import Image

from sable.app import build_sim_app


def _app(transitions=True):
    app = build_sim_app(frames_dir=None)
    app.display.ascii_preview = False
    app.settings._data.setdefault("display", {})["transitions"] = transitions
    return app


def test_fade_starts_on_screen_change():
    app = _app(True)
    app.go("clock")          # fsm.go -> render: establishes last_frame + rendered
    assert app._rendered_screen == "clock"
    app.go("menu")           # screen changed -> a fade should be in flight
    assert app._fade is not None
    assert app._rendered_screen == "menu"


def test_no_fade_when_disabled():
    app = _app(False)
    app.go("clock")
    app.go("menu")
    assert app._fade is None


def test_fade_presents_intermediate_frame():
    """The frame the display receives mid-fade is a genuine dissolve -- neither
    the pure old (clock) nor the pure new (menu) frame."""
    import time
    from PIL import ImageDraw
    app = _app(True)
    captured = []
    app.display.present = lambda im: captured.append(im.copy())
    app.go("clock")
    clock_frame = captured[-1].copy()
    app.go("menu")                                  # fade begins (alpha ~0)
    app._fade = (clock_frame, time.monotonic() - app._fade_s * 0.5)  # force ~50%
    app.render()
    mid = captured[-1]
    pure = Image.new("L", (256, 64), 0)
    app.fsm.current.render(pure, ImageDraw.Draw(pure), 256, 64)
    assert list(mid.getdata()) != list(clock_frame.getdata())
    assert list(mid.getdata()) != list(pure.getdata())


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print("\nAll %d crossfade tests passed." % len(tests))


if __name__ == "__main__":
    main()
