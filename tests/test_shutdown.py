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


def test_button_8_resolves_to_shutdown_by_default():
    """The bug that made the power button dead on a fresh install: settings.py
    shipped btn_8 as "none", and _get_button_action treats ANY truthy configured
    action as an override -- so the string "none" beat the built-in "shutdown"
    and the journal read `button 8 pressed: cmd=none`."""
    import json

    from sable.inputs.buttons import ButtonsLeds

    path = os.path.join(tempfile.gettempdir(), "sable_btn8_settings.json")
    if os.path.exists(path):
        os.unlink(path)
    settings = Settings(path=path)          # fresh file -> defaults are written
    app = type("A", (), {"settings": settings})()
    btns = ButtonsLeds.__new__(ButtonsLeds)
    btns._app = app
    (cmd, _arg), _led = btns._get_button_action(8)
    assert cmd == "shutdown", "button 8 resolved to %r" % (cmd,)

    # ...and a unit that already wrote the broken value gets migrated, since a
    # saved file always beats DEFAULTS.
    with open(path) as fh:
        data = json.load(fh)
    data["buttons"]["btn_8"] = {"action": "none", "arg": ""}
    data["_meta"] = {"rev": 1}
    with open(path, "w") as fh:
        json.dump(data, fh)
    migrated = Settings(path=path)
    assert migrated.get("buttons", "btn_8", "action") == "shutdown"
    assert migrated.get("_meta", "rev") == 2

    # A deliberate reassignment is not clobbered by the migration.
    data["buttons"]["btn_8"] = {"action": "menu", "arg": ""}
    data["_meta"] = {"rev": 1}
    with open(path, "w") as fh:
        json.dump(data, fh)
    assert Settings(path=path).get("buttons", "btn_8", "action") == "menu"


def test_poweroff_falls_back_when_volumio_ignores_the_event():
    """emit() succeeding is not the machine going down. python-socketio only
    raises when the client is disconnected, so a Volumio that simply does not
    act on the event used to leave the Pi blanked and running forever."""
    import subprocess

    import sable.app as app_mod

    app, _ = _app()
    app.dry_run = False

    class _Sio:
        def __init__(self):
            self.events = []

        def emit(self, name, *a):
            self.events.append(name)      # accepted, and does nothing else

    sio = _Sio()
    app.listener = type("L", (), {"sio": sio})()

    calls = []
    real_call, real_grace = subprocess.call, app_mod._VOLUMIO_POWEROFF_GRACE_S
    subprocess.call = lambda argv, **kw: (calls.append(argv), 0)[1]
    app_mod._VOLUMIO_POWEROFF_GRACE_S = 0.05
    try:
        app._poweroff()
    finally:
        subprocess.call = real_call
        app_mod._VOLUMIO_POWEROFF_GRACE_S = real_grace

    assert sio.events == ["shutdown"], "Volumio was never asked first"
    assert calls, "no poweroff command was run after Volumio ignored the event"
    assert "poweroff" in " ".join(calls[0])


def test_poweroff_tries_later_commands_when_sudo_is_refused():
    """A sudoers rule that failed visudo validation at install time makes every
    sudo form exit non-zero. That must not stop the remaining candidates."""
    import subprocess

    app, _ = _app()
    app.dry_run = False
    app.listener = None

    calls = []

    def _call(argv, **kw):
        calls.append(argv)
        return 1 if argv[0] == "sudo" else 0

    real_call = subprocess.call
    subprocess.call = _call
    try:
        app._poweroff()
    finally:
        subprocess.call = real_call

    assert len(calls) > 1, "gave up after the first refusal: %r" % (calls,)
    assert calls[-1][0] != "sudo", "never tried a non-sudo poweroff"


def test_shutdown_screen_is_not_the_boot_splash():
    app, _ = _app()
    app.handle("shutdown")
    time.sleep(0.05)
    assert app.fsm.current.name == "shutdown"
    assert app.fsm.current is not app.fsm.screens["splash"]
