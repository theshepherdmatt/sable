"""Self-tests for the play_uri button action.

A bare {"uri": ...} is not enough for a stream URL: Volumio has no service to
route it to and silently does nothing -- no playback, no toast, no error. And
even once it plays, Volumio only reports back a station logo if one was handed
to it, so the panel shows the generic placeholder. Both are pinned here.

Run: pytest tests/test_play_uri.py
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sable.app import _split_uri_arg
from sable.volumio.listener import VolumioListener

_BBC = ("http://a.files.bbci.co.uk/ms6/live/3441A116-B12E-4D2F-ACA8-C1984642FA4B"
        "/audio/simulcast/hls/nonuk/pc_hd_abr_v2/ak/bbc_radio_one.m3u8")
_ART = "https://cdn-radiotime-logos.tunein.com/s24939q.png"


class _Sio:
    def __init__(self):
        self.sent = []

    def emit(self, event, payload=None):
        self.sent.append((event, payload))


def _listener():
    lis = VolumioListener.__new__(VolumioListener)
    lis.sio = _Sio()
    lis.log = lambda *a: None
    return lis


def test_http_stream_is_sent_as_a_routable_webradio_item():
    lis = _listener()
    lis.play_uri(_BBC, title="BBC Radio 1", albumart=_ART)
    event, item = lis.sio.sent[0]
    assert event == "replaceAndPlay"
    # service is the bit that was missing: without it Volumio does nothing.
    assert item["service"] == "webradio"
    assert item["type"] == "webradio"
    assert item["uri"] == _BBC
    assert item["title"] == "BBC Radio 1"
    assert item["albumart"] == _ART, "no artwork -> panel shows the placeholder"


def test_stream_without_extras_still_plays():
    lis = _listener()
    lis.play_uri(_BBC)
    _event, item = lis.sio.sent[0]
    assert item["service"] == "webradio"
    assert item["title"], "Volumio needs some title to display"
    assert "albumart" not in item, "do not invent an artwork key we have no value for"


def test_non_http_uris_keep_their_own_service():
    """mpd:/spotify:/favourites URIs carry their service in the scheme already --
    guessing 'webradio' for them would break routing that currently works."""
    for uri in ("mpd:/some/track.flac", "spotify:track:xyz", "favourites/radio"):
        lis = _listener()
        lis.play_uri(uri)
        _event, item = lis.sio.sent[0]
        assert item == {"uri": uri}, "rewrote a non-http URI: %r" % (item,)


def test_arg_splits_on_pipes():
    assert _split_uri_arg(_BBC) == (_BBC, None, None)
    assert _split_uri_arg("%s | BBC Radio 1" % _BBC) == (_BBC, "BBC Radio 1", None)
    assert _split_uri_arg("%s | BBC Radio 1 | %s" % (_BBC, _ART)) == (
        _BBC, "BBC Radio 1", _ART)
    # Empty fields are not titles/artwork.
    assert _split_uri_arg("%s |  | %s" % (_BBC, _ART)) == (_BBC, None, _ART)
