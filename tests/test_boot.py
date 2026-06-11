"""Slice 6 self-test: the boot splash renders and animates. Run directly."""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PIL import Image, ImageDraw

from sable.app import build_sim_app


def test_splash_renders_and_scanner_animates():
    app = build_sim_app(frames_dir=None)
    app.display.ascii_preview = False
    app.go("splash")
    splash = app.fsm.screens["splash"]

    def frame_at(elapsed):
        splash._t0 = time.monotonic() - elapsed   # render sees ~`elapsed` seconds
        img = Image.new("L", (256, 64), 0)
        splash.render(img, ImageDraw.Draw(img), 256, 64)
        return list(img.getdata())

    a = frame_at(0.0)                              # scanner centred
    b = frame_at(0.4)                              # scanner moved
    assert any(a), "splash blank"
    assert a != b, "scanner did not move"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print("\nAll %d boot tests passed." % len(tests))


if __name__ == "__main__":
    main()
