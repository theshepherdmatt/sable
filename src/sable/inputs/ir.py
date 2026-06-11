"""IR remote listener: reads the LIRC daemon socket and maps keys to Sable's ONE
command vocabulary, calling the same app.handle the rotary and IPC use.

Runs in-process on hardware (like the rotary). Replaces quadify's ir_listener.py.
lircd broadcasts each key to every connected client, so this coexists with any
other listener without stealing events.

MODE-AWARE mapping (ported from quadify's proven scheme): the same key does the
natural thing for the current screen -- on a now-playing/clock screen OK is
play/pause and LEFT/RIGHT skip tracks; in a menu/browse list OK/RIGHT select,
LEFT goes back, UP/DOWN scroll.

Volume/mute are OPEN-LOOP: this unit's volume is the EVO Sabre DAC's own (Volumio
mixer_type=None) and the remote drives the DAC directly, so Sable does not change
volume -- it just flashes a VOLUME +/- (or MUTE) overlay as feedback.
"""
import os
import socket
import threading
import time

_LIRC_SOCKETS = ("/run/lirc/lircd", "/var/run/lirc/lircd")

# Screens where the remote acts as a TRANSPORT (not a list navigator).
_NOWPLAYING = ("clock", "modern", "spectrum")
# Commands that may auto-repeat when a key is held.
_REPEATABLE = ("scroll", "volume")


def command_for(key, mode):
    """Map an IR KEY_* name to (command, arg) for the current screen `mode`.
    Returns None for keys we ignore."""
    nowplaying = mode in _NOWPLAYING
    in_list = mode in ("menu", "browse")
    if key in ("KEY_OK", "KEY_ENTER"):
        return ("toggle", None) if nowplaying else ("select", None)
    if key == "KEY_RIGHT":
        return ("next", None) if nowplaying else ("select", None)
    if key == "KEY_LEFT":
        return ("previous", None) if nowplaying else ("back", None)
    if key == "KEY_UP":
        return ("scroll", -1)            # lists scroll; now-playing ignores it
    if key == "KEY_DOWN":
        return ("scroll", 1)
    if key == "KEY_MENU":
        return ("home", None) if in_list else ("menu", None)
    if key in ("KEY_BACK", "KEY_EXIT", "KEY_RETURN"):
        return ("back", None)
    if key == "KEY_HOME":
        return ("home", None)
    if key == "KEY_NEXT":
        return ("next", None)
    if key == "KEY_PREVIOUS":
        return ("previous", None)
    if key in ("KEY_PLAY", "KEY_PAUSE", "KEY_PLAYPAUSE"):
        return ("toggle", None)
    if key == "KEY_INPUT":
        return ("dac_input", None)
    if key == "KEY_VOLUMEUP":
        return ("volume", "+")
    if key == "KEY_VOLUMEDOWN":
        return ("volume", "-")
    if key == "KEY_MUTE":
        return ("mute", None)
    # KEY_POWER deliberately unmapped (avoid accidental shutdown).
    return None


class IrListener(threading.Thread):
    def __init__(self, handle, app=None, debounce_s=0.12, log=print):
        super().__init__(daemon=True, name="sable-ir")
        self.handle = handle
        self._app = app           # for the current screen (mode-aware mapping)
        self.debounce_s = debounce_s
        self.log = log
        self._running = True
        self._last = {}

    def _mode(self):
        try:
            return self._app.fsm.current.name if self._app else ""
        except Exception:
            return ""

    @staticmethod
    def _socket_path():
        for p in _LIRC_SOCKETS:
            if os.path.exists(p):
                return p
        return None

    def run(self):
        path = self._socket_path()
        if path is None:
            self.log("ir: LIRC socket not found; IR disabled")
            return
        while self._running:
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(path)
                with s.makefile("r") as f:
                    self.log("ir: listening on", path)
                    for line in f:
                        if not self._running:
                            break
                        self._on_line(line)
            except OSError as e:
                if not self._running:
                    break
                self.log("ir: socket error, retrying:", e)
                time.sleep(1.0)

    def _on_line(self, line):
        # lircd line: "<code> <repeat_hex> <KEY_NAME> <remote>"
        parts = line.strip().split()
        if len(parts) < 3:
            return
        key = parts[2]
        try:
            repeat = int(parts[1], 16)
        except ValueError:
            repeat = 0
        mapping = command_for(key, self._mode())
        if mapping is None:
            return
        cmd, arg = mapping
        # Auto-repeat only for hold-friendly commands (scroll, volume); the rest
        # fire once per press.
        if repeat > 0 and cmd not in _REPEATABLE:
            return
        now = time.monotonic()
        if now - self._last.get(key, 0.0) < self.debounce_s:
            return
        self._last[key] = now
        try:
            self.handle(cmd, arg)
        except Exception as e:
            self.log("ir: handle error", cmd, e)

    def stop(self):
        self._running = False
