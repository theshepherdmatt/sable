"""Self-tests for the built-in station presets.

The dropdown in the plugin and the table Sable plays from are two halves of one
feature: a label offered in the UI whose key is not in PRESETS is a button that
silently does nothing when pressed. These pin that they agree, and that a preset
dispatches down the same path as a hand-entered URI.

Run: pytest tests/test_stations.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sable import stations

_UICONFIG = os.path.join(os.path.dirname(__file__), "..", "plugin",
                         "UIConfig.json")


def _selects():
    """{button number: the select node} for the eight button-action dropdowns."""
    with open(_UICONFIG, encoding="utf-8") as fh:
        cfg = json.load(fh)
    found = {}

    def walk(node):
        if isinstance(node, dict):
            el_id = str(node.get("id") or "")
            if (node.get("element") == "select" and el_id.startswith("btn")
                    and el_id.endswith("_action")):
                found[int(el_id[3:-len("_action")])] = node
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(cfg)
    return found


def test_every_preset_has_a_stream_and_artwork():
    assert stations.PRESETS, "no presets defined"
    for key, (label, uri, art) in stations.PRESETS.items():
        assert label and not label.startswith("bbc_"), "%s: unlabelled" % key
        assert uri.startswith("http"), "%s: bad stream %r" % (key, uri)
        # Artwork is not optional: without it Volumio reports the generic
        # "/albumart" placeholder and the panel has no logo to draw.
        assert art.startswith("http"), "%s: bad artwork %r" % (key, art)


def test_preset_keys_are_unique_and_do_not_shadow_commands():
    """A preset key colliding with a real command (play/pause/shutdown...) would
    hijack it -- App.handle checks PRESETS before the command branches."""
    for reserved in ("play", "pause", "next", "previous", "shutdown", "none",
                     "play_uri", "play_playlist", "menu", "home"):
        assert reserved not in stations.PRESETS


def test_each_button_offers_only_its_own_fm4_station():
    """The point of the FM4 layout: button 1 is the BBC 1 button, so its
    dropdown reads like the legend on the panel rather than listing every
    station. A stale entry left behind by the generator breaks this."""
    selects = _selects()
    assert set(selects) == set(range(1, 9)), "expected 8 button selects"
    known = set(stations.PRESETS)
    for btn, sel in selects.items():
        offered = [o["value"] for o in sel["options"] if o["value"] in known]
        expected = list(stations.BUTTON_STATIONS[btn])
        assert offered == expected, (
            "button %d offers %r, expected %r" % (btn, offered, expected))
        # Nothing station-shaped that the table has never heard of.
        strays = [o["value"] for o in sel["options"]
                  if o["value"].startswith("bbc_") and o["value"] not in known]
        assert not strays, "button %d has orphaned entries: %r" % (btn, strays)


def test_ilr_and_power_buttons_offer_no_stations():
    """ILR was regional commercial radio -- no built-in for those, they take a
    URL. Button 8 is power."""
    selects = _selects()
    known = set(stations.PRESETS)
    for btn in (2, 4, 8):
        offered = [o["value"] for o in selects[btn]["options"] if o["value"] in known]
        assert offered == [], "button %d should offer no stations" % btn


def test_every_button_keeps_the_ordinary_actions():
    for btn, sel in _selects().items():
        values = {o["value"] for o in sel["options"]}
        for action in ("play", "pause", "play_uri", "shutdown", "none"):
            assert action in values, "button %d lost %r" % (btn, action)


def test_ui_labels_match_the_table():
    by_key = {k: v[0] for k, v in stations.PRESETS.items()}
    for sel in _selects().values():
        for opt in sel["options"]:
            if opt["value"] in by_key:
                assert opt["label"] == by_key[opt["value"]], (
                    "label drift on %s" % opt["value"])


def test_get_returns_title_uri_art_and_none_for_unknown():
    title, uri, art = stations.get("bbc_radio_4")
    assert title == "BBC Radio 4"
    assert "bbc_radio_fourfm" in uri
    assert art.startswith("https://")
    assert stations.get("not_a_station") is None
