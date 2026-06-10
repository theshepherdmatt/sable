"""Throwaway probe: dump Volumio browse data shapes so we can design BrowseScreen.
Connects read-only, asks for sources + a few focused browses, prints compact JSON.
"""
import json
import time

import socketio

sio = socketio.Client(reconnection=False)
seen = {}


def _short(data):
    """Trim a browseLibrary response to the bits we care about."""
    if not isinstance(data, dict):
        return data
    nav = data.get("navigation", {})
    lists = nav.get("lists", [])
    out = []
    for lst in lists if isinstance(lists, list) else []:
        items = lst.get("items", []) if isinstance(lst, dict) else []
        out.append({
            "title": lst.get("title") if isinstance(lst, dict) else None,
            "n": len(items),
            "sample": [
                {k: it.get(k) for k in ("title", "uri", "type", "service")}
                for it in items[:3] if isinstance(it, dict)
            ],
        })
    return {"lists": out, "prev": nav.get("prev")}


@sio.on("pushBrowseSources")
def _sources(data):
    seen["sources"] = [
        {k: s.get(k) for k in ("name", "uri", "plugin_name")}
        for s in (data or []) if isinstance(s, dict)
    ]


@sio.on("pushBrowseLibrary")
def _lib(data):
    uri = (data or {}).get("navigation", {}).get("info", {}).get("uri") if isinstance(data, dict) else None
    seen.setdefault("browses", []).append(_short(data))


sio.connect("http://localhost:3000")
sio.emit("getBrowseSources")
time.sleep(1.5)
for uri in ("playlists", "favourites", "radio"):
    sio.emit("browseLibrary", {"uri": uri})
    time.sleep(1.5)
sio.disconnect()
print(json.dumps(seen, indent=2, ensure_ascii=True)[:4000])
