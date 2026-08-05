#!/usr/bin/env python3
"""Sync the station presets from src/sable/stations.py into plugin/UIConfig.json.

The dropdown options and the table Sable actually plays from must agree -- a
label in the UI with no matching key in PRESETS is a button that does nothing.
Rather than maintain twelve stations x eight buttons of JSON by hand, generate
them. Idempotent: run it after editing stations.py.

    python3 tools/gen-station-options.py

Preserves the file's CRLF line endings (it is checked in with them; rewriting
it LF turns a small change into a whole-file diff).
"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

from sable import stations  # noqa: E402

UICONFIG = os.path.join(ROOT, "plugin", "UIConfig.json")
# Station entries sit between the generic actions and the terminal "Disabled"
# entry, so the list reads: transport, navigation, custom URI, stations, off.
ANCHOR = "none"


# The non-station actions. Anything in a button dropdown that is not one of
# these was put there by this script and is ours to replace or delete.
GENERIC_ACTIONS = frozenset({
    "play", "pause", "toggle", "next", "previous", "random", "repeat",
    "menu", "home", "play_uri", "play_playlist", "shutdown", "none",
})


def _is_generic(value):
    return value in GENERIC_ACTIONS


def PRESET_LABEL(key):
    return stations.PRESETS[key][0]


def _button_id(el_id):
    """'btn3_action' -> 3. None for anything else."""
    if not isinstance(el_id, str) or not el_id.startswith("btn"):
        return None
    if not el_id.endswith("_action"):
        return None
    try:
        return int(el_id[3:-len("_action")])
    except ValueError:
        return None


def main():
    raw = open(UICONFIG, "rb").read()
    crlf = b"\r\n" in raw
    cfg = json.loads(raw.decode("utf-8"))
    # Identify the entries we own by what we do NOT own: matching against
    # PRESETS cannot clear a station that has since been deleted from the table
    # (the key is gone, so nothing recognises the leftover option), which left
    # five orphans in every dropdown on the first run of this.
    keys = None  # see _is_generic
    touched = 0

    def walk(node):
        nonlocal touched
        if isinstance(node, dict):
            opts = node.get("options")
            btn = _button_id(node.get("id"))
            if (btn and node.get("element") == "select" and isinstance(opts, list)
                    and any(o.get("value") == ANCHOR for o in opts)):
                wanted = stations.ui_options(btn)
                kept = [o for o in opts if _is_generic(o.get("value"))]
                at = next(i for i, o in enumerate(kept)
                          if o.get("value") == ANCHOR)
                node["options"] = kept[:at] + [dict(o) for o in wanted] + kept[at:]
                touched += 1
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(cfg)
    out = json.dumps(cfg, indent=2) + "\n"
    if crlf:
        out = out.replace("\n", "\r\n")
    open(UICONFIG, "wb").write(out.encode("utf-8"))
    for b in sorted(stations.BUTTON_STATIONS):
        names = [PRESET_LABEL(k) for k in stations.BUTTON_STATIONS[b]]
        print("  button %d: %s" % (b, ", ".join(names) or "(no stations)"))
    print("updated %d button selects" % touched)


if __name__ == "__main__":
    main()
