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
import os
import sqlite3
import threading
import time
import urllib.parse

from mpd import MPDClient, ConnectionError as MPDConnectionError

from ..backoff import backoff_delay


def _guess_service(path):
    p = (path or "").strip()
    if p.lower().startswith(("http://", "https://")):
        return "webradio"
    if p:
        return "mpd"
    return ""


# moOde's own name for the library folder its radio stations live in.
_RADIO_DIR = "RADIO"

# moOde's station logo library, and the database that maps a stream URL to the
# station it belongs to. Both are moOde's, read-only, on the same box.
_MOODE_DB = os.environ.get("SABLE_MOODE_DB", "/var/local/www/db/moode-sqlite3.db")
_RADIO_LOGO_DIR = os.environ.get("SABLE_RADIO_LOGO_DIR",
                                 "/var/local/www/imagesw/radio-logos")
_RADIO_LOGO_URL = "/imagesw/radio-logos"

_stations = {}            # stream url -> station name
_stations_t = 0.0
_stations_lock = threading.Lock()


def _station_name_for(url):
    """Station name for a stream URL, from moOde's cfg_radio table.

    Why the database and not the tags: MPD reports NO `name` field for these
    (verified -- currentsong is just file/title/pos/id), and `title` only holds
    the station name until the stream's ICY metadata arrives and overwrites it
    with the current track. The URL never changes, so it is the one stable key.
    Opened read-only; a missing or unreadable database just means no logo.
    """
    global _stations, _stations_t
    if not url:
        return ""
    with _stations_lock:
        now = time.monotonic()
        # Reload on a miss (stations can be added in moOde), but at most once a
        # minute so an unknown URL cannot hammer the database every frame.
        if url not in _stations and (now - _stations_t) > 60.0:
            _stations_t = now
            try:
                con = sqlite3.connect("file:%s?mode=ro" % _MOODE_DB, uri=True)
                try:
                    _stations = {r[0]: r[1] for r in
                                 con.execute("select station, name from cfg_radio")}
                finally:
                    con.close()
            except Exception:
                pass
        return _stations.get(url, "")


def _radio_logo_url(name):
    """Host-relative URL of moOde's logo for a station, or "" if it has none.

    Prefers the _sm thumbnail (~3KB vs ~56KB) -- the panel draws it at 56px, so
    the full-size one is pure download. Checked on disk rather than just built,
    so a station with no logo falls back to Sable's own placeholder instead of
    showing a broken fetch.
    """
    if not name:
        return ""
    for rel in ("thumbs/%s_sm.jpg" % name, "%s.jpg" % name):
        try:
            if os.path.isfile(os.path.join(_RADIO_LOGO_DIR, rel)):
                return "%s/%s" % (_RADIO_LOGO_URL, urllib.parse.quote(rel, safe="/"))
        except OSError:
            pass
    return ""


def _albumart_url(path, song):
    """Where moOde's own web server (port 80) serves the art for this track.

    MPD itself reports no artwork, so without this the pushState carried no
    'albumart' key at all and moOde ALWAYS fell back to the vinyl placeholder.
    Volumio hands Sable a ready-made path instead; this is that path's moOde
    equivalent, and stays a host-relative "/..." so AlbumArtCache.resolve_url
    prefixes the configured host exactly as it does for Volumio.

    Two different sources, because they genuinely are two different things:
      - local files -> /coverart.php/<path>, which returns an embedded/folder
        cover already scaled down (~19KB vs ~263KB for the ?path= form).
      - webradio    -> the station-logo library, keyed on the station name.
        coverart.php does answer for a stream URL, but only with moOde's
        generic default cover, which is worse than Sable's own placeholder.
    """
    if not path:
        return ""
    if "://" in path:
        # Resolve the station by URL first; fall back to the tags, which hold the
        # station name only until ICY metadata replaces them mid-stream.
        name = (_station_name_for(path)
                or (song.get("name") or "").strip()
                or (song.get("title") or "").strip())
        return _radio_logo_url(name)
    return "/coverart.php/%s" % urllib.parse.quote(path, safe="/")


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
        "albumart": _albumart_url(path, song),
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
        # Whether the MPD connection is live RIGHT NOW. The boot gate polls this
        # to decide the player is up; VolumioListener answers the same question
        # with `.sio.connected`, which this class has no equivalent of -- so
        # without this flag the gate saw False forever and burned its full
        # timeout (~120s of splash) on every single moOde boot.
        self.connected = False
        self._client = MPDClient()
        self._client.timeout = 10
        self._lock = threading.Lock()
        # A SECOND, command-only MPD connection.
        #
        # _run() spends nearly all its time parked in `self._client.idle()`,
        # which blocks until MPD reports a change -- and it holds self._lock for
        # the whole wait. Any command sharing that lock therefore blocks on the
        # INPUT thread until unrelated activity happens to wake idle(): opening
        # the music library froze the whole UI until something else changed the
        # player. MPD is happy to serve several connections, so commands get
        # their own and never contend with idle() at all.
        self._cmd = MPDClient()
        self._cmd.timeout = 10
        self._cmd_lock = threading.Lock()
        self._cmd_connected = False
        # Browse callbacks -- same contract as VolumioListener (BrowseScreen /
        # HomeScreen read these); moOde has no async push, so browse()/
        # get_sources() call back synchronously right after fetching.
        self.on_browse = None
        self.on_sources = None
        self.on_connect = None  # set by the app: fires once per successful connect
        # moOde keeps its ~230 radio stations as .pls files in the library's
        # RADIO folder, so they were only reachable by drilling Music Library ->
        # RADIO. Advertise the folder as a source in its own right; the on-device
        # menu picks the same entry up via radio_source (VolumioListener has no
        # such attribute -- Volumio's browse root already offers Radio).
        self.radio_source = {"name": "Radio", "uri": _RADIO_DIR, "plugin_type": "mpd"}
        self.browse_sources = [{"name": "Music Library", "uri": "/", "plugin_type": "mpd"},
                               dict(self.radio_source)]

    # --- lifecycle ---
    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="sable-moode")
        self._thread.start()

    def stop(self):
        self._running = False
        self.connected = False
        with self._lock:
            try:
                self._client.disconnect()
            except Exception:
                pass
        with self._cmd_lock:
            self._cmd_connected = False
            try:
                self._cmd.disconnect()
            except Exception:
                pass

    def _run(self):
        attempt = 0
        while self._running:
            try:
                with self._lock:
                    self._client.connect(self.host, self.port)
                self.log("moode: connected", "%s:%d" % (self.host, self.port))
                self.connected = True
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
                self.connected = False
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

    def _run_cmd(self, what, fn, default=None):
        """Run fn(client) on the command connection, connecting on first use and
        reconnecting once if the socket went away (moOde restarts mpd on config
        changes, so a stale connection is routine, not exceptional).

        Never raises: an input-thread caller has nothing useful to do with the
        exception, and letting one escape would kill the button/IR/rotary thread.
        """
        with self._cmd_lock:
            for attempt in (1, 2):
                try:
                    if not self._cmd_connected:
                        self._cmd.connect(self.host, self.port)
                        self._cmd_connected = True
                    return fn(self._cmd)
                except (MPDConnectionError, ConnectionError, OSError) as exc:
                    try:
                        self._cmd.disconnect()
                    except Exception:
                        pass
                    self._cmd_connected = False
                    if attempt == 2:
                        self.log("moode: %s error:" % what, exc)
                except Exception as exc:
                    self.log("moode: %s error:" % what, exc)
                    return default
        return default

    def _push_state(self):
        status = self._run_cmd("status", lambda c: c.status())
        song = self._run_cmd("currentsong", lambda c: c.currentsong())
        if status is None:
            return
        d = _mpd_state_to_pushstate(status, song or {}, prev_volume=self.store.get().volume)
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
        # MPD paths are relative and have no leading slash: lsinfo("/NAS")
        # returns NOTHING while lsinfo("NAS") returns the folder. The home
        # carousel's own root entry is "/", so normalise rather than trusting
        # callers to know MPD's rule.
        path = (uri or "").lstrip("/")
        entries = self._run_cmd("browse", lambda c: c.lsinfo(path), default=[]) or []
        items = []
        # RADIO is a real library folder, so it shows up in the root listing --
        # but it is already the carousel's own Radio source, and offering the
        # same 233 stations twice just makes Music Library noisier. Hide it at
        # the ROOT only; browsing into RADIO deliberately still works, which is
        # exactly what the carousel entry does.
        hide_at_root = {_RADIO_DIR.lower()} if not path else set()
        for e in entries:
            if "directory" in e:
                leaf = e["directory"].rsplit("/", 1)[-1]
                if leaf.lower() in hide_at_root:
                    continue
                items.append({"uri": e["directory"], "type": "folder",
                             "title": leaf, "service": "mpd"})
            elif "file" in e:
                items.append({"uri": e["file"], "type": "song",
                             "title": e.get("title") or e["file"].rsplit("/", 1)[-1],
                             "service": _guess_service(e["file"])})
            elif "playlist" in e:
                # Strip the .pls extension: every one of moOde's ~230 stations is
                # a playlist file, and "BBC Radio 6 Music.pls" is not a station
                # name anyone wants to read off a 256px panel.
                leaf = e["playlist"].rsplit("/", 1)[-1]
                if leaf.lower().endswith(".pls"):
                    leaf = leaf[:-4]
                items.append({"uri": e["playlist"], "type": "playlist",
                             "title": leaf, "service": "mpd"})
        # Hand BrowseScreen the shape it actually parses. It runs every response
        # through _items_from_response(), which reads navigation.lists[].items[]
        # -- a bare list has no "navigation" key, so it flattened to nothing and
        # the music library opened EMPTY on moOde however many entries MPD
        # returned. Wrapping here keeps the screens backend-agnostic, which is
        # the whole point of the listener boundary (see DESIGN.md).
        parent = path.rsplit("/", 1)[0] if "/" in path else ""
        cb = self.on_browse
        if cb:
            try:
                cb({"navigation": {"lists": [{"items": items}],
                                   "prev": {"uri": parent}}})
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
        # A playlist file needs `load`, NOT `add`. MPD rejects add() on a .pls
        # outright ("No such directory"), which is every one of moOde's radio
        # stations -- so selecting a station did nothing at all. load() expands
        # it and queues the stream URL inside. Verified against mpd on moOde 10:
        # add("RADIO/x.pls") fails, load("RADIO/x.pls") queues the stream.
        is_playlist = ((item or {}).get("type") == "playlist"
                       or uri.lower().endswith((".pls", ".m3u", ".m3u8")))

        def _go(c):
            c.clear()
            if is_playlist:
                c.load(uri)
            else:
                c.add(uri)
            c.play(0)
        self._run_cmd("play_item", _go)

    def play_all(self, items):
        items = [it for it in (items or []) if it.get("uri")]
        if not items:
            return
        def _go(c):
            c.clear()
            for it in items:
                c.add(it["uri"])
            c.play(0)
        self._run_cmd("play_all", _go)

    def set_volume(self, arg):
        """arg = '+' / '-' / 'mute' / 'unmute', or an int 0..100 -- same
        vocabulary as VolumioListener.set_volume."""
        def _go(c):
            cur = int(c.status().get("volume", 0))
            if arg == "+":
                c.setvol(min(100, cur + 5))
            elif arg == "-":
                c.setvol(max(0, cur - 5))
            elif arg == "mute":
                self._last_volume = cur
                c.setvol(0)
            elif arg == "unmute":
                c.setvol(getattr(self, "_last_volume", 50) or 50)
            else:
                c.setvol(max(0, min(100, int(arg))))
        self._run_cmd("set_volume", _go)

    def transport(self, cmd):
        """play / pause / toggle / next / previous / random / repeat -- the same
        vocabulary app.TRANSPORT hands to the `volumio` CLI on Volumio. moOde has
        no such CLI (Sable used to shell out to `volumio` on every platform and
        died with "[Errno 2] No such file or directory: 'volumio'"), so the
        commands go down the MPD connection we already hold.

        Called on the INPUT thread (button/IR/rotary), so it must not block for
        long -- these are all single round-trips to a local socket, unlike the
        `volumio` CLI which has been seen to take ~17s under AirPlay.
        """
        def _go(c):
            if cmd == "play":
                c.play()
            elif cmd == "pause":
                c.pause(1)
            elif cmd == "toggle":
                # Resolve it ourselves rather than using MPD's bare `pause`
                # toggle: from "stop" that does nothing at all, which is
                # exactly the state a button press is most likely to hit.
                if c.status().get("state") == "play":
                    c.pause(1)
                else:
                    c.play()
            elif cmd == "next":
                c.next()
            elif cmd == "previous":
                c.previous()
            elif cmd in ("random", "repeat"):
                # Both are toggles in Sable's vocabulary; MPD wants an explicit
                # 0/1, so read the current value and invert it.
                cur = c.status().get(cmd)
                getattr(c, cmd)(0 if str(cur) == "1" else 1)
            else:
                self.log("moode: unknown transport cmd:", cmd)
        self._run_cmd("transport %s" % cmd, _go)

    def force_reconnect(self):
        self.log("moode: forcing self-disconnect (reconnect test)")
        with self._lock:
            try:
                self._client.disconnect()
            except Exception:
                pass
