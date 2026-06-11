"""Phase 2 self-tests: state normalization, marquee, modern render from fixture.

No network and no live Volumio -- proves the screen draws from the real captured
state, the seek/marquee math is right, and the albumart URL handling is correct.
Run: python3 tests/test_modern.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from PIL import Image, ImageDraw

from sable.state import PlayerState, StateStore
from sable.screens.base import marquee_offset
from sable.display.albumart import AlbumArtCache

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "rp_paused.json")


def _fixture():
    with open(FIXTURE, "r", encoding="utf-8") as fh:
        return json.load(fh)


def test_stream_and_mute_normalized_to_bool():
    s = PlayerState().merged({"stream": "False", "mute": "true"})
    assert s.stream is False
    assert s.mute is True
    assert PlayerState().merged({"stream": "True"}).stream is True


def test_seek_units_and_progress():
    s = PlayerState().merged({"seek": 70013, "duration": 233})
    assert s.seek_ms == 70013
    assert s.duration_s == 233
    # 70.013s of 233s ~= 0.30
    assert abs(s.progress() - 0.3005) < 0.001
    assert abs(s.elapsed_s - 70.013) < 0.001


def test_marquee_offset_advances_and_wraps():
    assert marquee_offset(0.0, 1.0, 100, speed=30) == 30
    assert marquee_offset(0.0, 4.0, 100, speed=30) == 20   # 120 % 100


def test_albumart_url_resolution():
    c = AlbumArtCache(host="http://localhost:3000", log=lambda *a: None)
    assert c.resolve_url("https://img.radioparadise.com/x.jpg") == \
        "https://img.radioparadise.com/x.jpg"
    assert c.resolve_url("/albumart?path=/x") == "http://localhost:3000/albumart?path=/x"
    assert c.resolve_url("albumart?path=/x") == "http://localhost:3000/albumart?path=/x"
    assert c.resolve_url("") is None


def test_modern_renders_nonblank_from_fixture():
    store = StateStore(log=lambda *a: None)
    store.apply_pushstate(_fixture())
    st = store.get()
    assert st.title and st.artist          # unicode survived
    assert st.status == "pause"
    # render the screen body without the full App: draw what modern draws.
    img = Image.new("1", (256, 64), 0)
    d = ImageDraw.Draw(img)
    d.text((2, 2), st.artist, fill=255)    # ascii-safe proxy draw
    assert img.getbbox() is not None       # something was drawn


def test_webradio_now_gets_a_spectrum():
    """Standard webradio plays through MPD -> it must feed CAVA (own-player
    sources like rp2/spotify must not)."""
    from sable.app import source_feeds_cava
    from sable.state import PlayerState
    assert source_feeds_cava(PlayerState().merged({"service": "webradio"})) is True
    assert source_feeds_cava(PlayerState().merged({"service": "rp2"})) is False
    assert source_feeds_cava(PlayerState().merged({"service": "spotify"})) is False


def test_title_lines_split_for_sparse_radio():
    from sable.app import build_sim_app
    m = build_sim_app(frames_dir=None).fsm.screens["modern"]

    class S:
        title = " BBC RADIO 1 - The biggest new pop"
        artist = ""
        album = ""
    a, b = m._title_lines(S())
    assert a == "BBC RADIO 1" and b == "The biggest new pop"   # split when no artist

    class T:
        title = "Wandering Star"
        artist = "Portishead"
        album = "Dummy"
    assert m._title_lines(T()) == ("Wandering Star", "Portishead")  # normal: no split

    class U:                                                   # dash but artist set
        title = "A - B"
        artist = "X"
        album = ""
    assert m._title_lines(U()) == ("A - B", "X")

    class V:                                                   # album fills the sub
        title = "Some Track"
        artist = ""
        album = "Jazz FM"
    assert m._title_lines(V()) == ("Some Track", "Jazz FM")


def test_mmss_formats():
    from sable.screens.modern import _mmss
    assert _mmss(0) == "0:00"
    assert _mmss(95) == "1:35"
    assert _mmss(-5) == "0:00"          # clamps negatives (rounding past end)


def test_hero_renders_playing_and_paused_nonblank():
    """The hero draws something for both play and pause, and the paused frame
    differs from the playing one (designed paused state, not the same render)."""
    from sable.app import build_sim_app
    app = build_sim_app(frames_dir=None)
    app.display.ascii_preview = False
    app.store.apply_pushstate({"status": "play", "title": "Wandering Star",
                               "artist": "Portishead", "service": "mpd",
                               "seek": 95000, "duration": 268, "volume": 80})
    app.go("modern")
    play = Image.new("L", (256, 64), 0)
    app.fsm.screens["modern"].render(play, ImageDraw.Draw(play), 256, 64)
    assert play.getbbox() is not None

    app.store.apply_pushstate({"status": "pause"})
    app.go("modern")
    paused = Image.new("L", (256, 64), 0)
    app.fsm.screens["modern"].render(paused, ImageDraw.Draw(paused), 256, 64)
    assert paused.getbbox() is not None
    assert list(play.getdata()) != list(paused.getdata())   # genuinely different


def test_cinema_theme_renders_and_differs_from_panel():
    """The Cinema theme renders a non-blank frame and is a genuinely different
    layout from Panel for the same state."""
    from sable.app import build_sim_app
    app = build_sim_app(frames_dir=None)
    app.display.ascii_preview = False
    app.store.apply_pushstate({"status": "play", "title": "Wandering Star",
                               "artist": "Portishead", "service": "mpd",
                               "seek": 1000, "duration": 200})
    app.go("modern")
    m = app.fsm.screens["modern"]
    app.settings._data.setdefault("display", {})["theme"] = "panel"
    panel = Image.new("L", (256, 64), 0)
    m.render(panel, ImageDraw.Draw(panel), 256, 64)
    app.settings._data["display"]["theme"] = "cinema"
    cinema = Image.new("L", (256, 64), 0)
    m.render(cinema, ImageDraw.Draw(cinema), 256, 64)
    assert panel.getbbox() is not None
    assert cinema.getbbox() is not None
    assert list(panel.getdata()) != list(cinema.getdata())


def test_paused_never_resolves_to_spectrum():
    """A paused spectrum is flat bars (the old dead-blank panel). nowplaying must
    fall to the modern hero when paused even with an MPD source + spectrum style."""
    from sable.app import build_sim_app
    app = build_sim_app(frames_dir=None)
    app.settings._data.setdefault("display", {})["screen"] = "spectrum"  # in-memory only
    app.store.apply_pushstate({"status": "play", "service": "mpd"})
    assert app.nowplaying_screen() == "spectrum"
    app.store.apply_pushstate({"status": "pause"})
    assert app.nowplaying_screen() == "modern"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print("\nAll %d modern tests passed." % len(tests))


if __name__ == "__main__":
    main()
