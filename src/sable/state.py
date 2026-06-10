"""Internal player state model -- the single object screens and the FSM read.

Volumio quirks are normalized ONCE here, on ingestion, so no screen re-parses raw
payloads (the old code parsed them in three places):
  - stream "False"/"True" (string) and mute -> real bool.
  - seek arrives in MILLISECONDS, duration in SECONDS. We keep seek_ms (ms) and
    duration_s (s) as the canonical fields and expose elapsed_s / progress() so
    every screen reads consistent units.
  - samplerate / bitdepth are left as the display strings Volumio already sends.
"""
import threading
import time
from dataclasses import asdict, dataclass, replace


def _as_bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.strip().lower() in ("true", "1", "yes")
    return bool(v)


@dataclass(frozen=True)
class PlayerState:
    status: str = "stop"          # play | pause | stop
    title: str = ""
    artist: str = ""
    album: str = ""
    service: str = ""             # mpd | webradio | rp2 | spop | ...
    uri: str = ""
    albumart: str = ""
    volume: int = 0
    mute: bool = False
    samplerate: str = ""
    bitdepth: str = ""
    seek_ms: int = 0              # canonical: milliseconds
    duration_s: int = 0          # canonical: seconds
    stream: bool = False         # is this a stream (webradio) -- normalized to bool

    def merged(self, d):
        """Copy with keys from a (possibly partial) Volumio dict applied. Missing
        or null keys keep the previous value, so a transient null never blanks the
        screen."""
        def pick(key, cur, cast):
            if key not in d or d[key] is None:
                return cur
            try:
                return cast(d[key])
            except (TypeError, ValueError):
                return cur
        return replace(
            self,
            status=pick("status", self.status, str),
            title=pick("title", self.title, str),
            artist=pick("artist", self.artist, str),
            album=pick("album", self.album, str),
            service=pick("service", self.service, str),
            uri=pick("uri", self.uri, str),
            albumart=pick("albumart", self.albumart, str),
            volume=pick("volume", self.volume, int),
            mute=pick("mute", self.mute, _as_bool),
            samplerate=pick("samplerate", self.samplerate, str),
            bitdepth=pick("bitdepth", self.bitdepth, str),
            seek_ms=pick("seek", self.seek_ms, int),
            duration_s=pick("duration", self.duration_s, int),
            stream=pick("stream", self.stream, _as_bool),
        )

    @property
    def elapsed_s(self):
        return self.seek_ms / 1000.0

    def progress(self, position_ms=None):
        """Fraction 0..1. Pass an extrapolated position_ms for a live seek bar."""
        if self.duration_s <= 0:
            return 0.0
        pos = self.seek_ms if position_ms is None else position_ms
        return max(0.0, min(1.0, pos / (self.duration_s * 1000.0)))

    def to_dict(self):
        return asdict(self)


class StateStore:
    def __init__(self, log=print):
        self._lock = threading.RLock()
        self._state = PlayerState()
        self._updated = time.monotonic()
        self._subs = []
        self.log = log

    def subscribe(self, callback):
        """callback(old_state, new_state) -- called on every applied change."""
        self._subs.append(callback)

    def get(self):
        with self._lock:
            return self._state

    def _set(self, new):
        old = self._state
        self._state = new
        self._updated = time.monotonic()
        return old

    def apply_pushstate(self, data):
        with self._lock:
            new = self._state.merged(data or {})
            old = self._set(new)
        self._notify(old, new)
        return new

    def apply_volume(self, payload):
        # Volumio emits either a bare int or {"vol": N, "mute": bool}.
        if isinstance(payload, dict):
            vol = payload.get("vol", payload.get("volume"))
            mute = payload.get("mute")
        else:
            vol, mute = payload, None
        d = {}
        if vol is not None:
            d["volume"] = vol
        if mute is not None:
            d["mute"] = mute
        with self._lock:
            new = self._state.merged(d)
            old = self._set(new)
        self._notify(old, new)
        return new

    def live_position_ms(self):
        """Extrapolate the play position between pushState events: while playing,
        add the wall time since the last update. Done HERE (one place) so the
        seek bar advances on the render tick without any per-screen thread."""
        with self._lock:
            st = self._state
            updated = self._updated
        if st.status == "play":
            pos = st.seek_ms + (time.monotonic() - updated) * 1000.0
        else:
            pos = st.seek_ms              # freeze while paused/stopped
        if st.duration_s > 0:
            pos = min(pos, st.duration_s * 1000.0)   # clamp at track end
        return pos

    def progress_fraction(self):
        return self.get().progress(self.live_position_ms())

    def _notify(self, old, new):
        for cb in list(self._subs):
            try:
                cb(old, new)
            except Exception as exc:
                self.log("state: subscriber error", exc)
