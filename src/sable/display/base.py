"""Display abstraction.

One interface, two backends: OledDisplay (real SSD1322 over SPI) and SimDisplay
(renders to PNG + an inline ASCII preview, touches no hardware). The app depends
only on this interface, so the same screens run on the bench and on the panel.

Canvas mode is "1" (1-bit) for the slice -- crisp text for clock/menus. The real
SSD1322 is 16-level grayscale; VU/spectrum screens can switch the canvas to "L"
later without changing this interface.
"""
from abc import ABC, abstractmethod

from PIL import Image


class Display(ABC):
    mode = "1"

    def __init__(self, width, height):
        self.width = width
        self.height = height

    def blank_canvas(self):
        return Image.new(self.mode, (self.width, self.height), 0)

    @abstractmethod
    def present(self, image):
        """Push a fully-rendered frame to the panel."""

    def set_contrast(self, value):
        pass

    def sleep(self):
        """Deep-sleep: power the panel off but keep the framebuffer."""

    def wake(self):
        pass

    def cleanup(self):
        pass
