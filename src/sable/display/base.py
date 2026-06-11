"""Display abstraction.

One interface, two backends: OledDisplay (real SSD1322 over SPI) and SimDisplay
(renders to PNG + an inline ASCII preview, touches no hardware). The app depends
only on this interface, so the same screens run on the bench and on the panel.

Canvas mode is "L" (8-bit greyscale): the SSD1322 is a native 16-level greyscale
panel, so album art, gradients and fades get the full grey ramp. Text and bars
stay razor-sharp because Screen.text()/draw_text_clipped() render glyph SHAPE
through a 1-bit mask (no anti-aliasing) and paste a chosen grey through it -- so
we keep type hierarchy without the soft AA halo a truetype draw on "L" would add.
The panel only ever sees 16 distinct greys; OledDisplay maps "L" -> the device's
"RGB" greyscale path (see oled.py), SimDisplay quantises previews to 16 levels so
nothing on the bench looks better than the hardware can do.
"""
from abc import ABC, abstractmethod

from PIL import Image


class Display(ABC):
    mode = "L"

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
