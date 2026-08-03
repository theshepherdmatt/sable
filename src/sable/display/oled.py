"""Real SSD1322 backend (luma over SPI).

NOT instantiated during Phase 0 bench testing: constructing it resets GPIO24/25
and the SPI bus, which would fight the live plugin. The slice always uses
SimDisplay. This file documents the hardware path for later deployment.

Carry-forward: dual-pin reset. luma drives RST=GPIO24 at runtime; on cleanup we
additionally pull GPIO25 low ~1s to force the panel dark on stop/shutdown.
"""
import time

from .base import Display


class OledDisplay(Display):
    def __init__(self, pins, rotate=0, log=print):
        # `rotate` is DEGREES (0/180) from the user's display.rotate setting; luma
        # wants quarter-turns (0-3). 180deg (panel mounted upside-down) -> 2.
        luma_rotate = (int(rotate) // 90) % 4
        super().__init__(pins.width, pins.height)
        from luma.core.interface.serial import spi
        from luma.oled.device import ssd1322
        import RPi.GPIO as GPIO

        self._GPIO = GPIO
        self._pins = pins
        self.log = log

        # Reset pulse the panel BEFORE init. BCM25 is the OLED's reset/blank line
        # (cleanup()/poweroff pulls it LOW to force the panel dark). At cold boot
        # it can power up already-high, so merely setting it HIGH is a no-op and
        # the panel never resets -> stays blank even though luma's write-only
        # SSD1322 init "succeeds" (no readback to fail on). Pulsing LOW->HIGH
        # forces a clean reset -- exactly what a manual `systemctl restart` does
        # (the prior process's cleanup drives it low, then this init raises it),
        # which is why a restart fixed the blank panel but a cold boot did not.
        try:
            GPIO.setmode(GPIO.BCM)
            GPIO.setwarnings(False)
            GPIO.setup(pins.blank, GPIO.OUT)
            GPIO.output(pins.blank, GPIO.LOW)
            time.sleep(0.3)
            GPIO.output(pins.blank, GPIO.HIGH)
            time.sleep(0.2)  # settle before SPI init
        except Exception as e:
            log("oled: blank-pin reset failed (continuing):", e)

        self._serial = spi(
            port=pins.spi_port,
            device=pins.spi_device,
            gpio_RST=pins.rst,
            gpio_DC=pins.dc,
        )
        # luma's ssd1322 packs 16-level greyscale ONLY via its "RGB" path
        # (_render_greyscale computes luma per pixel into 4 bits). mode="1" would
        # force its 1-bit on/off path and waste 15 of the panel's greys -- which
        # is exactly what made album art look blotchy. So the device is "RGB" and
        # present() converts our greyscale ("L") canvas to RGB just before the
        # blit. Render budget verified on this Pi 4: full-frame greyscale pack
        # ~16ms (~60fps ceiling), and diff_to_previous only repacks the changed
        # bounding box, so a typical now-playing frame is far cheaper.
        self._dev = ssd1322(self._serial, mode="RGB", rotate=luma_rotate)

    def present(self, image):
        # Screens draw on an "L" canvas; the device wants "RGB" for greyscale.
        if image.mode != "RGB":
            image = image.convert("RGB")
        self._dev.display(image)

    def set_contrast(self, value):
        self._dev.contrast(int(value))

    def sleep(self):
        self._dev.hide()

    def wake(self):
        self._dev.show()

    def cleanup(self):
        try:
            self._dev.cleanup()
        except Exception:
            pass
        # Force the panel dark via GPIO25 (carry-forward: dual-pin reset).
        try:
            g = self._GPIO
            g.setmode(g.BCM)
            g.setup(self._pins.blank, g.OUT)
            g.output(self._pins.blank, g.LOW)
            g.cleanup()
        except Exception:
            pass
