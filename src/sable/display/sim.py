"""Headless display backend.

Saves each frame as a PNG and prints an inline ASCII preview, so the boot/clock/
menu flow can be verified over SSH without ever opening SPI or resetting the live
panel. The 64x8 preview cells map 1:1 in aspect to the 256x64 panel (4x8 px per
cell), so the preview is geometrically faithful.
"""
import os

from .base import Display

RAMP = " .:-=+*#%@"


class SimDisplay(Display):
    def __init__(self, width, height, frames_dir=None, log=print, ascii_preview=True):
        super().__init__(width, height)
        self.frames_dir = frames_dir
        self.log = log
        self.ascii_preview = ascii_preview
        self.n = 0
        self._asleep = False
        if frames_dir:
            os.makedirs(frames_dir, exist_ok=True)

    def present(self, image):
        self.n += 1
        gray = image.convert("L")
        if self.frames_dir:
            gray.save(os.path.join(self.frames_dir, "%03d.png" % self.n))
        if self.ascii_preview:
            self._preview(gray)

    def _preview(self, gray):
        small = gray.resize((64, 8))
        px = small.load()
        tag = " [asleep]" if self._asleep else ""
        self.log("frame %03d  %dx%d%s" % (self.n, self.width, self.height, tag))
        self.log("+" + "-" * 64 + "+")
        for y in range(8):
            row = "".join(RAMP[min(9, px[x, y] * 9 // 255)] for x in range(64))
            self.log("|" + row + "|")
        self.log("+" + "-" * 64 + "+")

    def set_contrast(self, value):
        self.log("sim: contrast ->", value)

    def sleep(self):
        self._asleep = True
        self.log("sim: OLED sleep (hide / 0xAE)")

    def wake(self):
        self._asleep = False
        self.log("sim: OLED wake (show / 0xAF)")
