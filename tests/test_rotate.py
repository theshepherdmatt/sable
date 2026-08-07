"""Self-tests for the Screen Rotation menu entry.

Rotation is the one display setting whose value the user cannot judge from the
menu text -- they have to SEE the panel flip -- so the behaviour worth pinning
is that choosing it writes the setting AND pushes it at the panel immediately:
  - the entry exists in the root menu and offers Normal / Upside-down;
  - choosing one writes display.rotate in degrees (0/180), not luma quarter-turns;
  - the row's live value tracks the setting;
  - it is applied to the display, not merely saved for the next restart;
  - a backend that cannot rotate in place (SimDisplay) is tolerated rather than
    raising -- the sim and the tests must survive the same menu the panel gets;
  - reload_config re-applies it, so editing settings.json (the only settings
    surface moOde has) takes effect without a restart;
  - re-selecting the CURRENT value does not re-init the panel, which would cost
    a pointless full repaint.

Headless -- SimDisplay. Run: python3 tests/test_rotate.py
"""
import os
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sable import hardware
from sable.app import App
from sable.display.sim import SimDisplay
from sable.settings import Settings

_SETTINGS = os.path.join(tempfile.gettempdir(), "sable_rotate_settings.json")


def _app():
    """A throwaway app on its OWN settings file.

    Deliberately NOT build_sim_app(), which constructs a bare Settings() and so
    reads and WRITES the real config/settings.json -- these tests set rotation
    repeatedly, and against the shared file that both rotates the running
    service's panel and leaks state between tests in alphabetical order.
    """
    if os.path.exists(_SETTINGS):
        os.remove(_SETTINGS)
    settings = Settings(path=_SETTINGS)
    display = SimDisplay(hardware.OLED.width, hardware.OLED.height, frames_dir=None)
    return App(display, settings, dry_run=True, log=lambda *a: None)


def _menu(app):
    m = app.fsm.screens["menu"]
    m.on_enter()
    return m


def _labels(m):
    return [m._unpack(i)[0] for i in m._cur["items"]]


def _open(m, label):
    m._cur["index"] = _labels(m).index(label)
    m.handle_select()


def test_rotation_entry_exists_with_both_options():
    m = _menu(_app())
    assert "Screen Rotation" in _labels(m)
    _open(m, "Screen Rotation")
    assert _labels(m) == ["Normal", "Upside-down", "Back"]


def test_selecting_writes_degrees_not_quarter_turns():
    app = _app()
    m = _menu(app)
    _open(m, "Screen Rotation")
    _open(m, "Upside-down")
    # 180 degrees. A luma quarter-turn (2) here would silently rotate 90deg on
    # the real panel, since OledDisplay divides whatever it is given by 90.
    assert app.settings.get("display", "rotate") == 180
    _open(m, "Normal")
    assert app.settings.get("display", "rotate") == 0


def test_row_value_tracks_the_setting():
    app = _app()
    m = _menu(app)
    assert m._rotate_label() == "Normal"
    app.settings.set("display", "rotate", 180)
    assert m._rotate_label() == "180deg"


def test_garbage_setting_reads_as_normal():
    app = _app()
    m = _menu(app)
    for bad in (None, "", "later"):
        app.settings.set("display", "rotate", bad)
        assert m._rotate_label() == "Normal"


def test_applied_to_the_display_not_just_saved():
    app = _app()
    seen = []

    def _set_rotate(deg):
        seen.append(int(deg))
        return True

    app.display.set_rotate = _set_rotate
    m = _menu(app)
    _open(m, "Screen Rotation")
    _open(m, "Upside-down")
    assert seen == [180], "menu saved the setting but never pushed it at the panel"


def test_backend_without_rotation_is_tolerated():
    # SimDisplay has no set_rotate at all; the menu must not raise.
    app = _app()
    assert not hasattr(app.display, "set_rotate")
    m = _menu(app)
    _open(m, "Screen Rotation")
    _open(m, "Upside-down")
    assert app.settings.get("display", "rotate") == 180


def test_display_that_raises_is_tolerated():
    app = _app()

    def _boom(deg):
        raise RuntimeError("SPI busy")

    app.display.set_rotate = _boom
    app.settings.set("display", "rotate", 180)
    assert app.set_rotate_from_settings() is False   # logged, not raised


def test_reload_config_reapplies_rotation():
    app = _app()
    seen = []
    app.display.set_rotate = lambda deg: seen.append(int(deg)) or True
    app.settings.set("display", "rotate", 180)
    app.handle("reload_config")
    assert 180 in seen, "editing settings.json needs a restart to rotate"


def test_no_reinit_when_value_unchanged():
    # OledDisplay.set_rotate returns False when already current; the app must
    # then skip the repaint rather than treat it as a change.
    app = _app()
    app.display.set_rotate = lambda deg: False
    app.settings.set("display", "rotate", 0)
    assert app.set_rotate_from_settings() is False


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print("OK (%d tests)" % len(tests))


if __name__ == "__main__":
    main()
