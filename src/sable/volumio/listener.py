"""The single Volumio Socket.IO listener.

Owned by the display process. One connection for the whole of Sable; state is
fanned out to other processes over the command sockets (sable-controls gets
play/pause for the status LED). sable-controls does NOT open its own Volumio
connection.

Carry-forward from the old volumio_listener.py:
  - python-socketio 4.6.1 / engineio 3.14.2 (EIO3) -- Volumio speaks the old
    protocol; this pair is load-bearing.
  - Subscribe to pushState/pushTrack/pushBrowse*/pushToastMessage/volume.
  - Emit getState/getBrowseSources/browseLibrary as QUERIES only. We never emit
    a playback- or volume-changing command from here.
  - Single-flight reconnect with backoff: only the _run thread reconnects, so a
    socket bounce can never spawn a storm of competing reconnect threads.
"""
import threading
import time

import socketio


def backoff_delay(attempt, base=2.0, cap=60.0):
    """Reconnect delay for the Nth consecutive failure (1-based)."""
    return min(base * max(1, attempt), cap)


class VolumioListener:
    def __init__(self, store, url="http://localhost:3000", log=print):
        self.store = store
        self.url = url
        self.log = log
        self._running = False
        self._thread = None
        # Browse callbacks (set by the app so BrowseScreen gets results). Browse
        # responses are not request-correlated, so the consumer treats the next
        # pushBrowseLibrary as the answer to its last browse() call.
        self.on_browse = None
        self.on_sources = None
        self.browse_sources = []   # latest getBrowseSources result (HomeScreen reads it)
        self.sio = socketio.Client(reconnection=False)  # we own reconnect
        self._wire()

    def _wire(self):
        sio = self.sio

        @sio.on("connect")
        def _on_connect():
            self.log("volumio: connected", self.url)
            sio.emit("getState")          # query current state
            sio.emit("getBrowseSources")  # query available services

        @sio.on("disconnect")
        def _on_disconnect():
            self.log("volumio: disconnected")

        @sio.on("pushState")
        def _on_push_state(data):
            st = self.store.apply_pushstate(data or {})
            self.log("  pushState -> status=%s vol=%s title=%r service=%s"
                     % (st.status, st.volume, st.title, st.service))

        @sio.on("volume")
        def _on_volume(data):
            st = self.store.apply_volume(data)
            self.log("  volume -> %s (mute=%s)" % (st.volume, st.mute))

        @sio.on("pushTrack")
        def _on_push_track(data):
            title = data.get("title") if isinstance(data, dict) else data
            self.log("  pushTrack -> %r" % (title,))

        @sio.on("pushBrowseLibrary")
        def _on_push_browse_library(data):
            self.log("  pushBrowseLibrary -> received")
            cb = self.on_browse
            if cb:
                try:
                    cb(data)
                except Exception as exc:
                    self.log("  on_browse error:", exc)

        @sio.on("pushBrowseSources")
        def _on_push_browse_sources(data):
            n = len(data) if isinstance(data, list) else "?"
            self.log("  pushBrowseSources -> %s sources" % (n,))
            self.browse_sources = data if isinstance(data, list) else []
            cb = self.on_sources
            if cb:
                try:
                    cb(data or [])
                except Exception as exc:
                    self.log("  on_sources error:", exc)

        @sio.on("pushToastMessage")
        def _on_toast(data):
            msg = data.get("message") if isinstance(data, dict) else data
            self.log("  pushToastMessage -> %r" % (msg,))

    # --- lifecycle ---
    def start(self):
        self._running = True
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="sable-volumio")
        self._thread.start()

    def _run(self):
        attempt = 0
        while self._running:
            try:
                self.sio.connect(self.url)  # 4.6.1 blocks until connected or raises
                attempt = 0
                self.sio.wait()  # blocks until the socket drops
            except Exception as exc:
                self.log("volumio: connect error:", exc)
            if not self._running:
                break
            attempt += 1
            delay = backoff_delay(attempt)
            self.log("volumio: reconnect in %.0fs (attempt %d)" % (delay, attempt))
            end = time.monotonic() + delay
            while self._running and time.monotonic() < end:
                time.sleep(0.25)

    def stop(self):
        self._running = False
        try:
            self.sio.disconnect()
        except Exception:
            pass

    # --- safe queries only (NEVER playback/volume changes) ---
    def request_state(self):
        try:
            self.sio.emit("getState")
        except Exception:
            pass

    def browse(self, uri=""):
        try:
            self.sio.emit("browseLibrary", {"uri": uri})
        except Exception:
            pass

    def get_sources(self):
        try:
            self.sio.emit("getBrowseSources")
        except Exception:
            pass

    # --- the ONE place Sable initiates playback (everything else is read-only) ---
    def play_item(self, item):
        """Replace the queue with a browse item and play it. Used by BrowseScreen
        when the user selects a playable item (song / web radio / playlist) on the
        panel. `item` is a Volumio browse item dict ({uri, service, type, title})."""
        try:
            self.sio.emit("replaceAndPlay", item)
        except Exception:
            pass

    def set_volume(self, arg):
        """Adjust Volumio's volume. arg = '+' / '-' / 'mute' / 'unmute', or an int
        0..100. A no-op on devices with no software mixer (a DAC that owns volume),
        so it is safe to call everywhere."""
        try:
            self.sio.emit("volume", arg)
        except Exception:
            pass

    def play_all(self, items):
        """Play a whole album/list: replace the queue with the first leaf, then
        append the rest. `items` are Volumio browse leaves ({uri, service, type,
        title}). Mirrors quadify's Play-Album (replaceAndPlay + addToQueue)."""
        items = [it for it in (items or []) if it.get("uri")]
        if not items:
            return
        try:
            self.sio.emit("replaceAndPlay", items[0])
            for it in items[1:]:
                self.sio.emit("addToQueue", it)
        except Exception:
            pass

    def force_reconnect(self):
        """Test hook: drop our OWN connection to exercise the reconnect path."""
        self.log("volumio: forcing self-disconnect (reconnect test)")
        try:
            self.sio.disconnect()
        except Exception:
            pass


def _main():
    import argparse

    from ..state import StateStore

    ap = argparse.ArgumentParser(prog="sable.volumio.listener")
    ap.add_argument("--seconds", type=int, default=20)
    ap.add_argument("--self-cycle", action="store_true",
                    help="force one self-reconnect mid-run")
    ap.add_argument("--fanout", action="store_true",
                    help="push led_status to the sable-controls socket")
    args = ap.parse_args()

    store = StateStore()

    if args.fanout:
        from ..controls import CONTROLS_SOCKET
        from ..ipc import send

        def on_change(old, new):
            if old.status != new.status:
                try:
                    send("led_status", new.status, path=CONTROLS_SOCKET)
                    print("  fanout -> led_status:", new.status)
                except OSError:
                    print("  fanout: controls socket unavailable")

        store.subscribe(on_change)

    listener = VolumioListener(store)
    listener.start()

    half = max(1, args.seconds // 2)
    time.sleep(half)
    print("--- current state:", store.get().to_dict())

    if args.fanout:
        # Demonstrate the wire live without changing playback: push the CURRENT
        # status once so the LED is correct at startup.
        from ..controls import CONTROLS_SOCKET
        from ..ipc import send
        try:
            send("led_status", store.get().status, path=CONTROLS_SOCKET)
            print("  fanout -> led_status (initial):", store.get().status)
        except OSError:
            print("  fanout: controls socket unavailable")

    if args.self_cycle:
        listener.force_reconnect()

    time.sleep(args.seconds - half)
    listener.stop()
    print("--- final state:", store.get().to_dict())


if __name__ == "__main__":
    _main()
