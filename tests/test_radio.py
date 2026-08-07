"""Self-tests for the moOde radio menu.

moOde keeps its ~230 stations as .pls files inside the library's RADIO folder,
which makes three things easy to get wrong -- each has a test here:

  - .pls files need MPD's `load`, NOT `add`. add() on a playlist file is
    REJECTED outright ("No such directory"), so using it meant selecting a
    station silently did nothing at all. Verified against mpd on moOde 10.
  - the raw leaf name is "BBC Radio 6 Music.pls"; the panel should show the
    station, not the file.
  - the folder is invisible unless something points at it, so it has to appear
    both in the home carousel and in the on-device menu -- and the menu row must
    survive the listener being attached AFTER the screens are built.

Volumio must not grow a duplicate Radio row: its own browse root already offers
one, and its listener has no radio_source, so the row must stay absent.

Headless -- no MPD, no network, no hardware. Run: python3 tests/test_radio.py
"""
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sable.moode import listener as listener_mod

from sable import hardware
from sable.app import App
from sable.display.sim import SimDisplay
from sable.moode.listener import MoodeListener
from sable.screens.browse import _items_from_response
from sable.settings import Settings

_SETTINGS = os.path.join(tempfile.gettempdir(), "sable_radio_settings.json")


class _FakeClient:
    """Records the MPD calls a real client would have received."""

    def __init__(self, entries=None):
        self.calls = []
        self._entries = entries or []

    def lsinfo(self, path):
        self.calls.append(("lsinfo", path))
        return self._entries

    def clear(self):
        self.calls.append(("clear",))

    def add(self, uri):
        self.calls.append(("add", uri))

    def load(self, uri):
        self.calls.append(("load", uri))

    def play(self, index):
        self.calls.append(("play", index))


def _listener(entries=None):
    """A listener wired to a fake client -- never starts a thread or a socket."""
    L = MoodeListener.__new__(MoodeListener)          # no __init__: no MPDClient
    L.log = lambda *a: None
    L.host, L.port = "localhost", 6600
    L.on_browse = None
    L.on_sources = None
    client = _FakeClient(entries)
    L._run_cmd = lambda what, fn, default=None: fn(client)
    return L, client


def _app(listener=None):
    if os.path.exists(_SETTINGS):
        os.remove(_SETTINGS)
    app = App(SimDisplay(hardware.OLED.width, hardware.OLED.height, frames_dir=None),
              Settings(path=_SETTINGS), dry_run=True, log=lambda *a: None)
    app.listener = listener
    return app


def _labels(m):
    return [m._unpack(i)[0] for i in m._cur["items"]]


def test_playlist_uses_load_not_add():
    L, c = _listener()
    L.play_item({"uri": "RADIO/FluxFM - 60s.pls", "type": "playlist"})
    assert ("load", "RADIO/FluxFM - 60s.pls") in c.calls
    assert not any(k == "add" for k, *_ in c.calls), "add() is rejected for .pls"
    assert ("play", 0) in c.calls


def test_playlist_detected_by_extension_without_type():
    # Browse always sets type, but play_uri/button paths may not.
    for ext in (".pls", ".m3u", ".m3u8"):
        L, c = _listener()
        L.play_item({"uri": "RADIO/Station" + ext})
        assert ("load", "RADIO/Station" + ext) in c.calls, ext


def test_ordinary_song_still_uses_add():
    L, c = _listener()
    L.play_item({"uri": "NAS/a/b/01. Track.flac", "type": "song"})
    assert ("add", "NAS/a/b/01. Track.flac") in c.calls
    assert not any(k == "load" for k, *_ in c.calls)


def test_pls_extension_stripped_from_titles():
    L, c = _listener([{"playlist": "RADIO/BBC Radio 6 Music.pls"},
                      {"playlist": "RADIO/FluxFM - 60s.PLS"}])
    got = []
    L.on_browse = got.append
    L.browse("RADIO")
    items, _prev = _items_from_response(got[0])
    assert [i["title"] for i in items] == ["BBC Radio 6 Music", "FluxFM - 60s"]
    # the uri keeps the extension -- it is what load() is given
    assert items[0]["uri"] == "RADIO/BBC Radio 6 Music.pls"


def test_radio_is_a_carousel_source():
    L = MoodeListener(None, log=lambda *a: None)
    names = [s["name"] for s in L.browse_sources]
    assert names == ["Music Library", "Radio"]
    assert L.radio_source["uri"] == "RADIO"


def test_radio_is_NOT_in_the_menu():
    # The carousel is the ONE place Radio lives. It was briefly in the on-device
    # menu too; offering the same folder from three surfaces is clutter.
    m = _app(MoodeListener(None, log=lambda *a: None)).fsm.screens["menu"]
    m.on_enter()
    assert "Radio" not in _labels(m)


def test_radio_folder_hidden_from_the_library_root():
    # Same reason: RADIO is a real library folder, but the carousel already
    # offers it, so the root listing should not repeat it.
    L, c = _listener([{"directory": "NAS"}, {"directory": "RADIO"},
                      {"directory": "OSDISK"}])
    got = []
    L.on_browse = got.append
    L.browse("")
    items, _prev = _items_from_response(got[0])
    assert [i["title"] for i in items] == ["NAS", "OSDISK"]


def test_radio_folder_still_browsable_when_opened_directly():
    # Hiding it at the root must NOT make it unreachable -- opening it is
    # exactly what the carousel entry does.
    L, c = _listener([{"playlist": "RADIO/ABC Country.pls"}])
    got = []
    L.on_browse = got.append
    L.browse("RADIO")
    items, _prev = _items_from_response(got[0])
    assert [i["title"] for i in items] == ["ABC Country"]


def test_nested_folder_named_radio_is_kept():
    # The hide applies to the ROOT only, not to any folder that happens to be
    # called RADIO further down someone's library.
    L, c = _listener([{"directory": "NAS/RADIO"}])
    got = []
    L.on_browse = got.append
    L.browse("NAS")
    items, _prev = _items_from_response(got[0])
    assert [i["title"] for i in items] == ["RADIO"]


def _with_stations(mapping, logos=()):
    """Point the station lookup at a fake db map and a temp logo directory."""
    listener_mod._stations = dict(mapping)
    listener_mod._stations_t = time.monotonic()      # suppress the db reload
    d = os.path.join(tempfile.gettempdir(), "sable_radio_logos")
    shutil.rmtree(d, ignore_errors=True)
    os.makedirs(os.path.join(d, "thumbs"), exist_ok=True)
    for rel in logos:
        path = os.path.join(d, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "wb").close()
    listener_mod._RADIO_LOGO_DIR = d
    return d


def test_station_art_resolves_by_url_not_tags():
    # MPD reports NO 'name' for a stream and 'title' is overwritten by ICY
    # metadata mid-stream, so the URL is the only stable key.
    _with_stations({"http://s/x": "ABC Country"}, ["thumbs/ABC Country_sm.jpg"])
    art = listener_mod._albumart_url("http://s/x", {"title": "Some Artist - A Song"})
    assert art == "/imagesw/radio-logos/thumbs/ABC%20Country_sm.jpg"


def test_station_art_prefers_the_small_thumbnail():
    _with_stations({"http://s/x": "ABC Country"},
                   ["thumbs/ABC Country_sm.jpg", "ABC Country.jpg"])
    assert "_sm.jpg" in listener_mod._albumart_url("http://s/x", {})


def test_station_art_falls_back_to_full_size_logo():
    _with_stations({"http://s/x": "ABC Country"}, ["ABC Country.jpg"])
    assert listener_mod._albumart_url("http://s/x", {}) == \
        "/imagesw/radio-logos/ABC%20Country.jpg"


def test_station_with_no_logo_gets_no_art():
    # Better an empty string (Sable draws its own placeholder) than a URL that
    # 404s on every fetch.
    _with_stations({"http://s/x": "Obscure FM"}, [])
    assert listener_mod._albumart_url("http://s/x", {}) == ""


def test_unknown_station_falls_back_to_tags():
    _with_stations({}, ["thumbs/Tagged Station_sm.jpg"])
    art = listener_mod._albumart_url("http://s/unknown", {"name": "Tagged Station"})
    assert art == "/imagesw/radio-logos/thumbs/Tagged%20Station_sm.jpg"


def test_local_files_still_use_coverart_php():
    _with_stations({}, [])
    assert listener_mod._albumart_url("NAS/a/01. Track.flac", {}) == \
        "/coverart.php/NAS/a/01.%20Track.flac"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print("OK (%d tests)" % len(tests))


if __name__ == "__main__":
    main()
