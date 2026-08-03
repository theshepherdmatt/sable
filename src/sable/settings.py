"""Sable settings -- the ONE source of truth for user preferences.

A single JSON file, written atomically (tmp + fsync + rename) to survive power
loss. The Volumio UI is a read-only mirror: getUIConfig renders from this file,
setUIConfig writes it back and pings the app to reload. There is no second store.
"""
import json
import os
import tempfile
import threading

DEFAULTS = {
    "display": {
        "screen": "modern",          # modern|spectrum
        "theme": "panel",            # panel|cinema (when screen == modern)
        "spectrum_style": "bars",    # bars|dots|mirror|ribbon|vu (screen == spectrum)
        "brightness": "high",        # low|medium|high -> hardware.CONTRAST
        "transitions": True,         # crossfade between screens
        "rotate": 0,                 # panel rotation DEGREES (0 or 180). 180 =
                                     # mounted upside-down. Baked into the luma
                                     # device at init -> a change needs a restart.
    },
    "clock": {
        "show_seconds": False,
        "show_date": False,
    },
    "screensaver": {
        "clock_after_s": 300,        # paused/idle -> show the clock (0 = never)
        "dim_s": 120,                # idle -> dim (reduced contrast + pixel-shift)
        "idle_s": 3600,              # idle -> fade to OLED off (0 = never)
    },
    "dac": {
        # Persisted HINT only. Tracks the remote INPUT button; user-correctable.
        "input_index": 0,
    },
    "ir": {"enabled": True, "profile": "Xiaomi IR for TV box"},
    "controls": {"leds_enabled": True},
    "buttons": {
        "btn_1": {"action": "play", "arg": ""},
        "btn_2": {"action": "pause", "arg": ""},
        "btn_3": {"action": "previous", "arg": ""},
        "btn_4": {"action": "next", "arg": ""},
        "btn_5": {"action": "random", "arg": ""},
        "btn_6": {"action": "repeat", "arg": ""},
        "btn_7": {"action": "none", "arg": ""},
        "btn_8": {"action": "none", "arg": ""},
    },
    "_meta": {"rev": 1},
}

_DEFAULT_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "config", "settings.json",
)


def _deep_merge(base, overlay):
    """Overlay known keys onto a copy of base; ignore unknown keys."""
    out = dict(base)
    for k, v in (overlay or {}).items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        elif k in out:
            out[k] = v
    return out


class Settings:
    def __init__(self, path=None):
        self.path = path or _DEFAULT_PATH
        self._lock = threading.RLock()
        self._data = json.loads(json.dumps(DEFAULTS))
        self.load()

    def load(self):
        with self._lock:
            try:
                with open(self.path, "r") as fh:
                    raw = json.load(fh)
                self._data = _deep_merge(DEFAULTS, raw)
            except FileNotFoundError:
                self.save()
            except (ValueError, OSError):
                pass
            return self._data

    def save(self):
        with self._lock:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=os.path.dirname(self.path), suffix=".tmp")
            try:
                with os.fdopen(fd, "w") as fh:
                    json.dump(self._data, fh, indent=2, sort_keys=True)
                    fh.flush()
                    os.fsync(fh.fileno())
                os.replace(tmp, self.path)
            finally:
                if os.path.exists(tmp):
                    os.unlink(tmp)

    def get(self, *path, default=None):
        with self._lock:
            node = self._data
            for key in path:
                if not isinstance(node, dict) or key not in node:
                    return default
                node = node[key]
            return node

    def set(self, *path_and_value):
        *path, value = path_and_value
        with self._lock:
            node = self._data
            for key in path[:-1]:
                node = node.setdefault(key, {})
            node[path[-1]] = value
            self.save()

    def as_dict(self):
        with self._lock:
            return json.loads(json.dumps(self._data))
