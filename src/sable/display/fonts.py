"""Font loader.

Uses the system DejaVu fonts (present on Volumio/Bookworm), so Sable ships no
font assets for the slice. Bespoke faces (a 7-seg clock face, etc.) can be added
to assets/ later and registered here.
"""
import os

from PIL import ImageFont

_DIR = "/usr/share/fonts/truetype/dejavu"
_FILES = {
    "sans": "DejaVuSans.ttf",
    "sans_bold": "DejaVuSans-Bold.ttf",
    "mono": "DejaVuSansMono.ttf",
    "mono_bold": "DejaVuSansMono-Bold.ttf",
}


class Fonts:
    def __init__(self):
        self._cache = {}

    def get(self, name, size):
        key = (name, size)
        if key not in self._cache:
            path = os.path.join(_DIR, _FILES.get(name, _FILES["sans"]))
            try:
                self._cache[key] = ImageFont.truetype(path, size)
            except OSError:
                self._cache[key] = ImageFont.load_default()
        return self._cache[key]
