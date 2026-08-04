"""The single moOde (MPD) listener -- the moOde analogue of volumio/listener.py.

moOde has no Socket.IO/pushState API; Sable talks MPD's own protocol directly
(python-mpd2) via the `idle` command, which blocks until something changes
(player/mixer/etc) instead of polling. Same public interface as
VolumioListener (start/stop/set_volume/play_item/play_all/browse/get_sources)
and the same store.apply_pushstate() contract, so app.py and every screen are
unchanged -- only the wiring in app.py's run_hardware()/run_modern_live() picks
which listener class to import.

Carry-forward from Quoode's moode_listener.py (proven on this exact platform):
  - python-mpd2, host/port default localhost:6600.
  - idle() loop for change notification; status() + currentsong() snapshot on
    each wake, merged into one dict.
  - service/source guessed from the file path (http -> webradio, else mpd) --
    moOde has no Volumio-style service metadata to read.
"""
import threading
import time

from mpd import MPDClient, ConnectionError as MPDConnectionError

from ..backoff import backoff_delay


def _guess_service(path):
    p = (path or "").strip()
    if p.lower().startswith(("http://", "https://")):
        return "webradio"
    if p:
        return "mpd"
    return ""


def _mpd_state_to_pushstate(status, song, prev_volume=0):
    """Merge MPD status()+currentsong() into a Volumio-shaped pushState dict
    (see PlayerState.merged) -- the ONE place moOde's field names/units are
    translated, so state.py never needs to know moOde exists."""
    mpd_status = status.get("state", "stop")   # play | pause | stop
    vol_raw = status.get("volume", str(prev_volume))
    try:
        volume = int(vol_raw)
    except (TypeError, ValueError):
        volume = prev_volume
    mute = volume <= 0
    elapsed_s = float(status.get("elapsed", 0.0) or 0.0)
    duration_s = float(status.get("duration", 0.0) or 0.0)
    path = song.get("file", "")
    samplerate, bitdepth = "", ""
    audio = status.get("audio")  # "44100:24:2" (rate:bits:channels)
    if audio:
        parts = audio.split(":")
        if len(parts) >= 2:
            try:
                samplerate = "%.1f kHz" % (int(parts[0]) / 1000.0)
            except ValueError:
                pass
            bitdepth = "%s bit" % parts[1] if parts[1].isdigit() else ""
    title = song.get("title") or song.get("name") or path.rsplit("/", 1)[-1]
    return {
        "status": mpd_status,
        "title": title,
        "artist": song.get("artist", ""),
        "album": song.get("album", ""),
        "service": _guess_service(path),
        "uri": path,
        "volume": max(0, volume),
        "mute": mute,
        "samplerate": samplerate,
        "bitdepth": bitdepth,
        "seek": int(elapsed_s * 1000),
        "duration": int(duration_s),
        "stream": _guess_service(path) == "webradio",
    }


class MoodeListener:
    def __init__(self, store, host="localhost", port=6600, log=print):
        self.store = store
        self.host = host
        self.port = port
        self.log = log
        self._running = False
        self._thread = None
        self._client = MPDClient()
        self._client.timeout = 10
        self._lock = threading.Lock()
        # Browse callbacks -- same contract as VolumioListener (BrowseScreen /
        # HomeScreen read these); moOde has no async push, so browse()/
        # get_sources() call back synchronously right after fetching.
        self.on_browse = None
        self.on_sources = None
        self.on_connect = None  # set by the app: fires once per successful connect
        self.browse_sources = [{"name": "Music Library", "uri": "/", "plugin_type": "mpd"}]

    # --- lifecycle ---
    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="sable-moode")
        self._thread.start()

    def stop(self):
        self._running = False
        with self._lock:
            try:
                self._client.disconnect()
            except Exception:
                pass

    def _run(self):
        attempt = 0
        while self._running:
            try:
                with self._lock:
                    self._client.connect(self.host, self.port)
                self.log("moode: connected", "%s:%d" % (self.host, self.port))
                attempt = 0
                if self.on_connect:
                    try:
                        self.on_connect()
                    except Exception as exc:
                        self.log("on_connect callback error:", exc)
                self._push_state()
                while self._running:
                    with self._lock:
                        changes = self._client.idle()
                    if not self._running:
                        break
                    if changes:
                        self._push_state()
            except (MPDConnectionError, ConnectionError, OSError) as exc:
                self.log("moode: connect error:", exc)
            except Exception as exc:
                self.log("moode: idle loop error:", exc)
            finally:
                with self._lock:
                    try:
                        self._client.disconnect()
                    except Exception:
                        pass
            if not self._running:
                break
            attempt += 1
            delay = backoff_delay(attempt)
            self.log("moode: reconnect in %.0fs (attempt %d)" % (delay, attempt))
            end = time.monotonic() + delay
            while self._running and time.monotonic() < end:
                time.sleep(0.25)

    def _push_state(self):
        with self._lock:
            status = self._client.status()
            song = self._client.currentsong()
        d = _mpd_state_to_pushstate(status, song, prev_volume=self.store.get().volume)
        new = self.store.apply_pushstate(d)
        self.log("  moode state -> status=%s vol=%s title=%r service=%s"
                 % (new.status, new.volume, new.title, new.service))

    # --- safe queries ---
    def request_state(self):
        try:
            self._push_state()
        except Exception as exc:
            self.log("moode: request_state error:", exc)

    def browse(self, uri=""):
        """Synchronous MPD `lsinfo` translated into Volumio-shaped browse items
        ({uri, service, type, title}) and delivered via on_browse, same as
        VolumioListener's async pushBrowseLibrary."""
        try:
            with self._lock:
                entries = self._client.lsinfo(uri or "")
        except Exception as exc:
            self.log("moode: browse error:", exc)
            entries = []
        items = []
        for e in entries:
            if "directory" in e:
                items.append({"uri": e["directory"], "type": "folder",
                             "title": e["directory"].rsplit("/", 1)[-1], "service": "mpd"})
            elif "file" in e:
                items.append({"uri": e["file"], "type": "song",
                             "title": e.get("title") or e["file"].rsplit("/", 1)[-1],
                             "service": _guess_service(e["file"])})
            elif "playlist" in e:
                items.append({"uri": e["playlist"], "type": "playlist",
                             "title": e["playlist"].rsplit("/", 1)[-1], "service": "mpd"})
        cb = self.on_browse
        if cb:
            try:
                cb(items)
            except Exception as exc:
                self.log("  on_browse error:", exc)

    def get_sources(self):
        """moOde has no Volumio-style service list -- just the library root."""
        cb = self.on_sources
        if cb:
            try:
                cb(self.browse_sources)
            except Exception as exc:
                self.log("  on_sources error:", exc)

    # --- the ONE place Sable initiates playback ---
    def play_item(self, item):
        uri = (item or {}).get("uri")
        if not uri:
            return
        try:
            with self._lock:
                self._client.clear()
                self._client.add(uri)
                self._client.play(0)
        except Exception as exc:
            self.log("moode: play_item error:", exc)

    def play_all(self, items):
        items = [it for it in (items or []) if it.get("uri")]
        if not items:
            return
        try:
            with self._lock:
                self._client.clear()
                for it in items:
                    self._client.add(it["uri"])
                self._client.play(0)
        except Exception as exc:
            self.log("moode: play_all error:", exc)

    def set_volume(self, arg):
        """arg = '+' / '-' / 'mute' / 'unmute', or an int 0..100 -- same
        vocabulary as VolumioListener.set_volume."""
        try:
            with self._lock:
                cur = int(self._client.status().get("volume", 0))
                if arg == "+":
                    self._client.setvol(min(100, cur + 5))
                elif arg == "-":
                    self._client.setvol(max(0, cur - 5))
                elif arg == "mute":
                    self._last_volume = cur
                    self._client.setvol(0)
                elif arg == "unmute":
                    self._client.setvol(getattr(self, "_last_volume", 50) or 50)
                else:
                    self._client.setvol(max(0, min(100, int(arg))))
        except Exception as exc:
            self.log("moode: set_volume error:", exc)

    def force_reconnect(self):
        self.log("moode: forcing self-disconnect (reconnect test)")
        with self._lock:
            try:
                self._client.disconnect()
            except Exception:
                pass
