"""Very-early boot splash -- fills the 40-50s gap between power-on and
sable.service actually starting (it waits on network.target + volumio.service).

Deliberately standalone: no App/FSM/listener, just enough luma+PIL to open the
SSD1322 and draw a static "BOOTING..." message with a slow pulse, so the panel
isn't dark while Volumio/moOde boot underneath. sable-boot-splash.service
starts this as early as systemd allows; sable.service Conflicts= it, so
systemd stops this cleanly (SIGTERM -> releases SPI/GPIO) right before the
real app claims the display. See systemd/sable-boot-splash.service.tmpl.
"""
import math
import signal
import sys
import time

from . import hardware
from .display.fonts import Fonts
from .display.oled import OledDisplay

_STOP = False


def _stop(_signum, _frame):
    global _STOP
    _STOP = True


def main():
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    display = OledDisplay(hardware.OLED, rotate=0, log=print)
    fonts = Fonts()
    from PIL import Image, ImageDraw

    w, h = hardware.OLED.width, hardware.OLED.height
    t0 = time.monotonic()
    try:
        while not _STOP:
            img = Image.new("L", (w, h), 0)
            draw = ImageDraw.Draw(img)
            t = time.monotonic() - t0
            pulse = 140 + int(90 * (0.5 + 0.5 * math.sin(t * 2.0)))
            draw.text((w // 2, h // 2 - 6), "STARTING", font=fonts.get("sans_bold", 22),
                       fill=255, anchor="mm")
            dots = "." * (1 + int(t * 2) % 3)
            draw.text((w // 2, h // 2 + 16), dots, font=fonts.get("sans", 14),
                       fill=pulse, anchor="mm")
            display.present(img)
            time.sleep(0.15)
    finally:
        # Handoff to sable.service: do not call display.cleanup() (which pulls
        # GPIO25 LOW and sleeps 1s, fighting sable.service's display init).
        try:
            display.sleep()
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main() or 0)
