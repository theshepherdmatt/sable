"""Browse screen: Play-all decoration + selection. Run: python3 tests/test_browse.py"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sable.app import build_sim_app
from sable.screens.browse import _playables


class FakeListener:
    def __init__(self):
        self.played = None
        self.browse_sources = []

    def play_all(self, items):
        self.played = items

    def play_item(self, item):
        self.played = [item]

    def browse(self, uri):
        pass

    def get_sources(self):
        pass


def _songs(n):
    return [{"title": "T%d" % i, "type": "song", "uri": "u%d" % i} for i in range(n)]


def _resp(items):
    return {"navigation": {"lists": [{"items": items}]}}


def test_playables_filters_to_songs():
    items = _songs(3) + [{"title": "Sub", "type": "folder", "uri": "f"}]
    assert len(_playables(items)) == 3


def test_album_gets_play_all_at_top():
    app = build_sim_app(frames_dir=None)
    b = app.fsm.screens["browse"]
    b.loading = True
    b._replace_top = False
    b._pending_title = "Album"
    b.on_browse_data(_resp(_songs(4)))
    assert b._cur["items"][0].get("_play_all")
    assert b._cur["items"][0]["title"] == "Play all"


def test_single_track_no_play_all():
    app = build_sim_app(frames_dir=None)
    b = app.fsm.screens["browse"]
    b.loading = True
    b._replace_top = False
    b._pending_title = "X"
    b.on_browse_data(_resp(_songs(1)))
    assert not any(it.get("_play_all") for it in b._cur["items"])


def test_folders_only_no_play_all():
    app = build_sim_app(frames_dir=None)
    b = app.fsm.screens["browse"]
    b.loading = True
    b._replace_top = False
    b._pending_title = "Artist"
    b.on_browse_data(_resp([{"title": "Alb%d" % i, "type": "folder", "uri": "a%d" % i}
                            for i in range(3)]))
    assert not any(it.get("_play_all") for it in b._cur["items"])


def test_select_play_all_plays_the_songs():
    app = build_sim_app(frames_dir=None)
    app.listener = FakeListener()
    b = app.fsm.screens["browse"]
    b.stack = [{"title": "Album",
                "items": [{"title": "Play all", "_play_all": True}] + _songs(3),
                "index": 0}]
    b.loading = False
    b.handle_select()                       # index 0 == Play all
    assert app.listener.played is not None and len(app.listener.played) == 3


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print("\nAll %d browse tests passed." % len(tests))


if __name__ == "__main__":
    main()
