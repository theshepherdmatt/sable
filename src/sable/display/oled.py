"""Real SSD1322 backend (luma over SPI).

NOT instantiated during Phase 0 bench testing: constructing it resets GPIO24/25
and the SPI bus, which would fight the live plugin. The slice always uses
SimDisplay. This file documents the hardware path for later deployment.

Carry-forward: dual-pin reset. luma drives RST=GPIO24 at runtime; on cleanup we
additionally pull GPIO25 low ~1s to force the panel dark on stop/shutdown.
"""
import threading
import time

from .base import Display


class OledDisplay(Display):
    def __init__(self, pins, rotate=0, log=print):
        # `rotate` is DEGREES (0/180) from the user's display.rotate setting; luma
        # wants quarter-turns (0-3). 180deg (panel mounted upside-down) -> 2.
        luma_rotate = (int(rotate) // 90) % 4
        self._luma_rotate = luma_rotate
        super().__init__(pins.width, pins.height)
        from luma.core.interface.serial import spi
        from luma.oled.device import ssd1322
        import RPi.GPIO as GPIO

        self._GPIO = GPIO
        self._pins = pins
        self.log = log
        # SPI BUS LOCK. Every method below that talks to the panel takes this.
        #
        # The blit (present -> luma's display()) is a long, multi-part transfer:
        # per changed segment it drives DC low for a _set_position command burst,
        # then DC high for the pixel data. A contrast/wake/sleep command issued
        # from ANOTHER thread mid-blit toggles DC and injects command bytes into
        # the middle of that data stream. The controller then reads pixel data as
        # commands: the visible result is a corrupt band that never repaints
        # (luma's prev_image already believes that region is correct -- so the
        # stale pixels survive every later diff = "ghosting"), and a stray byte
        # landing on 0xAE blanks the panel outright.
        #
        # Those callers are real and concurrent: note_activity()/set_base_contrast()
        # run on the rotary, IR, buttons, IPC and Volumio-listener threads, while
        # the sable-tick thread is rendering at `fps`. App._render_lock does NOT
        # cover them -- it only serialises render() against itself. Owning the lock
        # HERE means the bus is safe no matter which thread calls in.
        #
        # Ordering is always App._render_lock -> this lock (never the reverse), so
        # there is no lock-order inversion to deadlock on.
        self._bus = threading.RLock()

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

        # reset_hold_time / reset_release_time are NOT cosmetic, and luma's
        # defaults for both are ZERO (verified on this Pi: luma.core 2.3.1,
        # spi(..., reset_hold_time=0, reset_release_time=0)).
        #
        # spi() performs its OWN reset on the RST pin when it is constructed --
        # which happens AFTER the careful pulse above, on the SAME physical pin
        # (rst and blank are both BCM25 on this wiring). With zero hold and zero
        # release that is a near-instantaneous LOW/HIGH glitch: it undoes the
        # good 0.3s reset we just did and gives the SSD1322 no time to complete
        # its internal power-on sequence before the init command burst arrives.
        # The controller is write-only, so every subsequent command "succeeds"
        # with nothing to fail on, the app renders happily at 20fps, and the
        # panel stays dark -- which is precisely the observed fault: a healthy
        # process, `screensaver: dim` in the log, and a blank screen.
        #
        # It survives a `systemctl restart` because the outgoing process's
        # cleanup() holds RST low for its own teardown, so the panel gets a long
        # low period either side of the glitch. A cold boot has no outgoing
        # process, which is why the fault follows power-ons specifically.
        self._serial = spi(
            port=pins.spi_port,
            device=pins.spi_device,
            gpio_RST=pins.rst,
            gpio_DC=pins.dc,
            reset_hold_time=0.1,
            reset_release_time=0.1,
        )
        # luma's ssd1322 packs 16-level greyscale ONLY via its "RGB" path
        # (_render_greyscale computes luma per pixel into 4 bits). mode="1" would
        # force its 1-bit on/off path and waste 15 of the panel's greys -- which
        # is exactly what made album art look blotchy. So the device is "RGB" and
        # present() converts our greyscale ("L") canvas to RGB just before the
        # blit. Render budget verified on this Pi 4: full-frame greyscale pack
        # ~16ms (~60fps ceiling), and diff_to_previous only repacks the changed
        # bounding box, so a typical now-playing frame is far cheaper.
        # Explicit framebuffer instance. luma's signature is
        # `def __init__(..., framebuffer=diff_to_previous(), ...)` -- a MUTABLE
        # DEFAULT ARGUMENT, so that one object is created at import time and
        # SHARED by every ssd1322 built in this process. A second OledDisplay in
        # the same process (boot splash -> app in one interpreter, a re-init after
        # an SPI error, the --blank-panel path) would inherit the previous
        # device's prev_image and believe the panel already shows the old frame,
        # so the first diffs skip regions that are actually stale -> ghosting.
        # Passing our own instance gives each device a clean diff state.
        from luma.core.framebuffer import diff_to_previous
        self._dev = ssd1322(self._serial, mode="RGB", rotate=luma_rotate,
                            framebuffer=diff_to_previous())
        # Explicit full-GRAM clear: the RES pin pulse above resets the SSD1322's
        # command registers but does NOT clear its display RAM. luma's diff_to_
        # previous only repacks the CHANGED bounding box on each present(), so
        # whatever the boot splash (or a prior process) last drew can persist as
        # a ghost under Sable's own first frames unless something unconditionally
        # overwrites the whole panel first. clear() bypasses the diff and does
        # exactly that -- observed as leftover "STARTING..." dots surviving the
        # boot-splash-to-clock handoff without this.
        try:
            self._dev.clear()
        except Exception as e:
            log("oled: post-init clear failed (continuing):", e)

    def reinit(self):
        """Redo the panel's whole power-on sequence: reset pulse, init burst,
        clear. Safe to call on a working panel (it just costs one full repaint).

        Why this has to exist: sable.service starts ~3s into boot, and at that
        point the SSD1322's supply has not settled. The init lands on a
        controller that is not ready and is simply lost -- and because the part
        is WRITE-ONLY there is no readback, no error, and no way to detect it.
        The app then renders at 20fps into a void: healthy process, sensible
        log, dark panel. Proven on this board -- an identical init run ~90s
        after power-on lights the panel, the same init at ~3s does not.

        Since the failure is undetectable, this is unconditional: re-init once
        after the rails have settled rather than try to sense a state we cannot
        read. On a panel that came up fine the cost is a single repaint."""
        with self._bus:
            try:
                g = self._GPIO
                g.setmode(g.BCM)
                g.setwarnings(False)
                g.setup(self._pins.blank, g.OUT)
                g.output(self._pins.blank, g.LOW)
                time.sleep(0.3)
                g.output(self._pins.blank, g.HIGH)
                time.sleep(0.2)
            except Exception as e:
                self.log("oled: reinit reset pulse failed (continuing):", e)
            from luma.core.framebuffer import diff_to_previous
            from luma.oled.device import ssd1322
            # Rebuild the device on the EXISTING serial: this re-runs luma's
            # init sequence, contrast and show() through public API. Reusing
            # self._serial avoids re-claiming the SPI/GPIO handles.
            self._dev = ssd1322(self._serial, mode="RGB",
                                rotate=self._luma_rotate,
                                framebuffer=diff_to_previous())
            try:
                self._dev.clear()
            except Exception as e:
                self.log("oled: reinit clear failed (continuing):", e)

    def present(self, image):
        # Screens draw on an "L" canvas; the device wants "RGB" for greyscale.
        if image.mode != "RGB":
            image = image.convert("RGB")
        with self._bus:
            self._dev.display(image)

    def clear(self):
        """Unconditionally black the whole panel AND resync luma's diff state."""
        with self._bus:
            self._dev.clear()

    def set_contrast(self, value):
        with self._bus:
            self._dev.contrast(int(value))

    def sleep(self):
        with self._bus:
            self._dev.hide()

    def wake(self):
        with self._bus:
            self._dev.show()

    def force_full_redraw(self):
        """Reset luma's diffing framebuffer's tracked 'previous frame' so the
        NEXT present() writes the ENTIRE panel, regardless of what diff_to_
        previous currently believes matches. Call at screen transitions (see
        App.render()): self-heals any segment that has fallen out of sync
        with the real GRAM, which showed up as ghosting -- old screen content
        surviving visibly under a new screen (e.g. boot-splash dots still lit
        under the clock). Deliberately NOT full_frame() on every present() --
        that unconditionally re-sends the whole panel every frame, which this
        wiring/panel could not sustain (showed up as glitching, then blanking).
        This is the cheap middle ground: one full write per actual screen
        change instead of either 'maybe-stale forever' or 'always full'."""
        with self._bus:
            fb = getattr(self._dev, "framebuffer", None)
            if fb is not None and hasattr(fb, "prev_image"):
                fb.prev_image = None

    def cleanup(self):
        with self._bus:
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
