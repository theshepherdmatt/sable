#!/usr/bin/env python3
"""DEV-ONLY icon renderer. Run on a dev box / the Pi to (re)generate the PNGs the
runtime blits. The runtime and installer never call this -- they ship the PNGs.

The SVG files in assets/icons/src/ are the design source of truth. To keep even
the dev step Cairo-free, this script draws the equivalent glyphs with Pillow
primitives. If you later need exact SVG fidelity, swap in cairosvg HERE only
(never in the runtime).
"""
import os

from PIL import Image, ImageDraw

OUT = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", "assets", "icons"))
SIZE = 14


def _canvas():
    img = Image.new("L", (SIZE, SIZE), 0)
    return img, ImageDraw.Draw(img)


def play():
    img, d = _canvas()
    d.polygon([(3, 2), (SIZE - 3, SIZE // 2), (3, SIZE - 2)], fill=255)
    return img


def pause():
    img, d = _canvas()
    d.rectangle((3, 2, 5, SIZE - 3), fill=255)
    d.rectangle((SIZE - 6, 2, SIZE - 4, SIZE - 3), fill=255)
    return img


def main():
    os.makedirs(OUT, exist_ok=True)
    for name, fn in (("play", play), ("pause", pause)):
        path = os.path.join(OUT, name + ".png")
        fn().save(path)
        print("wrote", path)


if __name__ == "__main__":
    main()
