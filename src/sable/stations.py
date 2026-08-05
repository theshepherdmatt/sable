"""Built-in radio station presets, selectable directly as button actions.

The alternative was making every user paste a 200-character HLS URL into the
settings page for something as ordinary as "put Radio 4 on button 7" -- and
paste it again for the artwork, since Volumio only reports back a station logo
if one was handed to it. A station is a name, not a URL, so the dropdown offers
it as a name and this table holds the rest.

Keeping them here (Python) rather than in the plugin's UIConfig means the URLs
live in one place: the settings page stores only the key ("bbc_radio_4"), which
round-trips cleanly back into the dropdown on reload, and a station whose stream
URL changes is fixed here without touching anyone's saved settings.

Streams are the 128k HLS variants from as-hls-ww-live.akamaized.net -- the same
ones Volumio's own radio browse serves, verified reachable from a UK Pi. Order
is the order the dropdown shows.
"""
from collections import OrderedDict

_HLS = ("http://as-hls-ww-live.akamaized.net/pool_{pool}/live/ww/{slug}"
        "/{slug}.isml/{slug}-audio%3d128000.norewind.m3u8")
_TUNEIN = "https://cdn-radiotime-logos.tunein.com/{id}.png"
# BBC Sounds' own icon, for the stations TuneIn has no clean logo for.
_SOUNDS = ("https://static.files.bbci.co.uk/sounds/web/sounds-web/img/"
           "sounds-apple-touch-icon.ead169771d.png")


def _bbc(pool, slug, tunein=None):
    return (_HLS.format(pool=pool, slug=slug),
            _TUNEIN.format(id=tunein) if tunein else _SOUNDS)


# key -> (label shown in the dropdown, stream uri, artwork uri)
PRESETS = OrderedDict()


def _add(key, label, pool, slug, tunein=None):
    uri, art = _bbc(pool, slug, tunein)
    PRESETS[key] = (label, uri, art)


_add("bbc_radio_1", "BBC Radio 1", "01505109", "bbc_radio_one", "s24939q")
_add("bbc_radio_2", "BBC Radio 2", "74208725", "bbc_radio_two", "s24940q")
_add("bbc_radio_3", "BBC Radio 3", "23461179", "bbc_radio_three", "s24941q")
_add("bbc_radio_4", "BBC Radio 4", "55057080", "bbc_radio_fourfm", "s25419q")
# BBC LR -- the FM4's local-radio button. Which local station is the only part
# of that legend the user has to choose.
_add("bbc_radio_london", "BBC Radio London", "98137350", "bbc_london")
_add("bbc_radio_scotland", "BBC Radio Scotland", "43322914",
     "bbc_radio_scotland_fm")
_add("bbc_radio_ulster", "BBC Radio Ulster", "31244774", "bbc_radio_ulster")


# Which stations each front-panel button may be set to, following the Quad FM4
# this hardware copies: BBC 1-4 down the left column, ILR 1/ILR 2 and BBC LR on
# the right, TUNE bottom-right.
#
#   1 BBC 1     2 ILR 1        A button with a fixed station on the FM4 offers
#   3 BBC 2     4 ILR 2        that one and nothing else, so its dropdown reads
#   5 BBC 3     6 BBC LR       like the legend printed on the panel. ILR was
#   7 BBC 4     8 TUNE         commercial radio and varied by region -- no
#                              built-in for those, so 2 and 4 take a URL via
# "Play Stream / URI". 8 is the power button. Every button keeps the ordinary
# transport actions alongside; this only governs the station entries.
BUTTON_STATIONS = {
    1: ("bbc_radio_1",),
    2: (),
    3: ("bbc_radio_2",),
    4: (),
    5: ("bbc_radio_3",),
    6: ("bbc_radio_london", "bbc_radio_scotland", "bbc_radio_ulster"),
    7: ("bbc_radio_4",),
    8: (),
}


def split_uri_arg(arg):
    """Split a button's play_uri argument into (uri, title, albumart).

    The settings page gives each button ONE free-text argument field, but a
    stream needs up to three things to present properly: Volumio will not route
    a bare URL at all, and it only reports back a station logo if one was handed
    to it. Rather than add two more fields per button to the plugin UI (sixteen
    boxes for a feature most people use once), the field accepts pipe-separated
    extras -- `URI | Station Name | logo.png` -- with everything after the URI
    optional. Pipes cannot appear in a URL, so this cannot misparse a plain one.

    Lives here rather than in app.py so the LED policy in inputs/buttons.py can
    read a button's URI without importing App (which imports buttons).
    """
    parts = [p.strip() for p in str(arg).split("|")]
    uri = parts[0]
    title = parts[1] if len(parts) > 1 and parts[1] else None
    albumart = parts[2] if len(parts) > 2 and parts[2] else None
    return uri, title, albumart


def button_uri(action, arg):
    """The stream a button would play, or "" if it plays no stream.

    Covers both kinds of radio button: a built-in preset (action is its key) and
    a hand-entered one (action is play_uri, the URL is in arg).
    """
    entry = PRESETS.get(action)
    if entry is not None:
        return entry[1]
    if action == "play_uri" and arg:
        return split_uri_arg(arg)[0]
    return ""


def get(key):
    """(title, uri, albumart) for a preset key, or None if it is not one."""
    entry = PRESETS.get(key)
    if entry is None:
        return None
    label, uri, art = entry
    return label, uri, art


def ui_options(button):
    """The station entries for one button's dropdown, in panel order.

    Empty for the ILR slots and the power button -- see BUTTON_STATIONS.
    """
    return [{"value": k, "label": PRESETS[k][0]}
            for k in BUTTON_STATIONS.get(button, ())]
