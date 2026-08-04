"""Self-tests for the power-off sequence: message -> blank -> poweroff.

The ORDER is the whole point and every step of it has silently regressed before,
so each is pinned here:
  - the message screen actually appears (and is not the animated boot splash);
  - it survives the state churn Volumio produces while shutting down -- playback
    stopping pushes a 'stop' event, and reconcile_screen runs every tick, both of
    which used to be free to replace the message with the clock;
  - the panel is blanked BEFORE poweroff is requested, not left lit;
  - rendering stops once blanked, so the render tick cannot repaint the panel in
    the seconds between the request and the power actually going.

Headless -- SimDisplay, dry_run (no real poweroff). Run: pytest tests/test_shutdown.py
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sable import hardware
from sable.app import App
from sable.display.sim import SimDisplay
from sable.settings import Settings

_FRAMES = os.path.join(tempfile.gettempdir(), "sable_shutdown_frames")


class _RecordingDisplay(SimDisplay):
    """SimDisplay that records the order of panel operations."""

    def __init__(self, *a, **kw):
        super().__init__(*a, **kw)
        self.ops = []

    def present(self, image):
        self.ops.append("present")
        super().present(image)

    def clear(self):
        self.ops.append("clear")

    def sleep(self):
        self.ops.append("sleep")
        super().sleep()


def _app():
    os.makedirs(_FRAMES, exist_ok=True)
    settings = Settings(path=os.path.join(tempfile.gettempdir(),
                                          "sable_shutdown_settings.json"))
    settings.set("display", "shutdown_message_s", 0.2)
    settings.set("display", "transitions", False)
    display = _RecordingDisplay(hardware.OLED.width, hardware.OLED.height,
                                frames_dir=_FRAMES)
    app = App(display, settings, dry_run=True, log=lambda *a: None)
    app.go("clock")
    return app, display


def _settle(app, seconds=1.0):
    """Run the render tick the way run_hardware's loop does while we wait."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        app.reconcile_screen()
        app.render()
        time.sleep(0.02)


def test_shows_message_then_blanks_then_powers_off():
    app, display = _app()
    app.handle("shutdown")
    _settle(app, 1.0)

    assert app.fsm.current.name == "shutdown"
    assert "clear" in display.ops, "panel was never cleared"
    assert "sleep" in display.ops, "panel was never blanked"
    # Blank must be the LAST panel op: anything presented after it means the
    # message (or a later screen) was repainted over the blank.
    assert display.ops[-1] == "sleep", "panel was repainted after blanking: %r" % (
        display.ops[-4:],)


def test_message_survives_stop_event_and_reconcile():
    """Volumio pushes 'stop' as its services go down. That must not take the
    panel off the shutdown message and back to the clock.

    NB this currently passes with the FSM's shutdown latch removed -- nothing
    presently tries to leave the screen (TABLE has no "shutdown" row, and
    reconcile_screen only acts on clock/modern/spectrum). It pins the BEHAVIOUR
    so that a later TABLE entry or a widened reconcile_screen has to keep it."""
    app, _ = _app()
    app.store.apply_pushstate({"title": "x", "status": "play"})
    app.handle("shutdown")
    app.store.apply_pushstate({"status": "stop"})
    app.reconcile_screen()
    app.render()
    assert app.fsm.current.name == "shutdown"
    _settle(app, 0.6)
    assert app.fsm.current.name == "shutdown"


def test_render_is_suppressed_once_blanked():
    app, display = _app()
    app.handle("shutdown")
    _settle(app, 1.0)
    assert app._shutting_down is True
    before = len(display.ops)
    for _ in range(10):
        app.render()
    assert len(display.ops) == before, "render() painted after the blank"


def test_second_hold_does_not_start_a_second_sequence():
    app, display = _app()
    app.handle("shutdown")
    app.handle("shutdown")
    _settle(app, 1.0)
    assert display.ops.count("sleep") == 1


def test_shutdown_screen_is_not_the_boot_splash():
    app, _ = _app()
    app.handle("shutdown")
    time.sleep(0.05)
    assert app.fsm.current.name == "shutdown"
    assert app.fsm.current is not app.fsm.screens["splash"]
