"""IR mode-aware key mapping + the volume/mute/input OSD.
Run: python3 tests/test_ir.py
"""
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PIL import Image, ImageDraw

from sable.inputs.ir import command_for
from sable.app import build_sim_app


def test_nowplaying_buttons():
    # On this remote: SELECT=KEY_MENU (open menu), PLAY/PAUSE=KEY_OK (toggle),
    # LEFT/RIGHT skip tracks.
    assert command_for("KEY_MENU", "modern") == ("menu", None)
    assert command_for("KEY_OK", "modern") == ("toggle", None)
    assert command_for("KEY_OK", "clock") == ("toggle", None)
    assert command_for("KEY_LEFT", "modern") == ("previous", None)
    assert command_for("KEY_RIGHT", "spectrum") == ("next", None)


def test_menu_buttons():
    # SELECT (KEY_MENU) and RIGHT select; LEFT backs out; UP/DOWN scroll;
    # PLAY/PAUSE (KEY_OK) still toggles playback.
    assert command_for("KEY_MENU", "menu") == ("select", None)
    assert command_for("KEY_RIGHT", "menu") == ("select", None)
    assert command_for("KEY_LEFT", "menu") == ("back", None)
    assert command_for("KEY_UP", "menu") == ("scroll", -1)
    assert command_for("KEY_DOWN", "browse") == ("scroll", 1)
    # KEY_OK: toggle by default (ApEvo's PLAY/PAUSE bar, SELECT is a separate
    # KEY_MENU button); "select" only for profiles in OK_SELECTS_PROFILES
    # (e.g. Xiaomi, whose center button IS the confirm button).
    assert command_for("KEY_OK", "menu") == ("toggle", None)
    assert command_for("KEY_OK", "menu", ok_selects=True) == ("select", None)
    assert command_for("KEY_OK", "modern", ok_selects=True) == ("toggle", None)
    assert command_for("KEY_PLAY", "menu", ok_selects=True) == ("toggle", None)


def test_dac_buttons_open_loop_or_ignored():
    assert command_for("KEY_VOLUMEUP", "modern") == ("volume", "+")
    assert command_for("KEY_VOLUMEDOWN", "menu") == ("volume", "-")
    assert command_for("KEY_MUTE", "modern") == ("mute", None)
    assert command_for("KEY_INPUT", "modern") is None          # DAC-only -> ignored
    assert command_for("KEY_POWER", "modern") is None          # never shuts down
    assert command_for("KEY_WHATEVER", "modern") is None


def _app():
    app = build_sim_app(frames_dir=None)
    app.display.ascii_preview = False
    app.go("clock")
    return app


def test_osd_overlay_changes_frame_then_expires():
    app = _app()
    base = Image.new("L", (256, 64), 0)
    app.fsm.current.render(base, ImageDraw.Draw(base), 256, 64)
    app.show_osd("+", "VOLUME", duration=999)
    assert app._osd is not None
    over = app._draw_osd(base.copy())
    assert list(over.getdata()) != list(base.getdata())        # overlay applied
    app._osd = ("+", "VOLUME", time.monotonic() - 1)           # force-expire
    out = app._draw_osd(base.copy())
    assert app._osd is None                                    # cleared
    assert list(out.getdata()) == list(base.getdata())         # nothing drawn


def test_volume_and_mute_commands_raise_osd():
    app = _app()
    app.handle("volume", "+")
    assert app._osd_volume and app._osd[1] == "VOLUME"         # live volume OSD
    app.handle("mute")
    assert app._osd[0] == "MUTE" and not app._osd_volume


def test_transport_suppressed_with_hint_during_airplay():
    app = _app()
    app.store.apply_pushstate({"status": "play", "service": "airplay_emulation",
                               "title": "Desire"})
    app._osd = None
    app.handle("pause")                         # PAUSE during AirPlay
    assert app._osd is not None and app._osd[0] == "AIRPLAY"   # hint, not a command
    # a normal MPD source is not suppressed (dry-run transport, no OSD)
    app.store.apply_pushstate({"status": "play", "service": "mpd", "title": "X"})
    app._osd = None
    app.handle("pause")
    assert app._osd is None


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print("\nAll %d IR tests passed." % len(tests))


if __name__ == "__main__":
    main()
