"""Icon cache: blit PRE-RENDERED PNGs via Pillow.

Runtime and install need NO Cairo. PNGs are generated at dev time by
tools/render_icons.py (from the SVG sources in assets/icons/src/) and committed
under assets/icons/. Missing icons return None -- callers fall back gracefully.
"""
import os

from PIL import Image

_DEFAULT_BASE = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "assets", "icons")
)


class IconCache:
    def __init__(self, base=None, log=print):
        self.base = base or _DEFAULT_BASE
        self.log = log
        self._cache = {}

    def get(self, name):
        if name in self._cache:
            return self._cache[name]
        path = os.path.join(self.base, name + ".png")
        try:
            img = Image.open(path).convert("L")
        except Exception:
            img = None
        self._cache[name] = img
        return img
