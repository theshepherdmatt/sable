"""IR remote listener: reads the LIRC daemon socket and maps keys to Sable's ONE
command vocabulary, calling the same app.handle the rotary and IPC use.

Runs in-process on hardware (like the rotary). Replaces quadify's ir_listener.py.
lircd broadcasts each key to every connected client, so this coexists with any
other listener without stealing events.

REMOTE PROFILE LIBRARY: Sable ships the Volumio ir_controller profile library
(config/lirc/profiles/<RemoteName>/lircd.conf, vendored by tools/update-ir-
profiles.sh) and a picker in the Volumio UI. The SHIPPED DEFAULT is the "Xiaomi
IR for TV box" remote; selecting a profile copies its lircd.conf to /etc/lirc and
restarts lircd. command_for below maps the KEY_* names to Sable commands and is
remote-agnostic (any profile emitting these names works).

REFERENCE MAPPING ("ApEvo" -- an Audiophonics EVO Sabre remote, verified with
irw). This profile maps six buttons to the UI; the rest drive that DAC directly:
    SELECT button      -> KEY_MENU   (NOT KEY_OK!)
    PLAY/PAUSE bar      -> KEY_OK
    D-pad UP/DOWN/LEFT/RIGHT -> KEY_UP/DOWN/LEFT/RIGHT
    VOL+ / VOL- / MUTE -> KEY_VOLUMEUP / KEY_VOLUMEDOWN / KEY_MUTE  (open-loop hint)
    POWER / INPUT      -> DAC only -- ignored

MODE-AWARE: the same button does the natural thing for the current screen. On a
now-playing/clock screen SELECT opens the menu, PLAY/PAUSE toggles, LEFT/RIGHT skip
tracks; in a menu/browse list SELECT/RIGHT select an item, LEFT goes back, UP/DOWN
scroll, PLAY/PAUSE still toggles playback.

Volume/mute are OPEN-LOOP: the DAC owns volume (Volumio mixer_type=None)
and the remote drives the DAC directly, so Sable does NOT change volume -- it just
flashes a VOLUME +/- (or MUTE) overlay as feedback.
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

# Remote profiles whose center button is the ONLY confirm/select key (no
# separate SELECT button reaching KEY_MENU/KEY_RIGHT with distinct intent) --
# for these, KEY_OK/KEY_ENTER means "select" in a list. Remotes like ApEvo have
# a dedicated PLAY/PAUSE bar on KEY_OK that must always toggle, even in a menu,
# because their SELECT button already sends KEY_MENU -- leave those out.
OK_SELECTS_PROFILES = frozenset({"Xiaomi IR for TV box"})


def command_for(key, mode, ok_selects=False):
    """Map an IR KEY_* name to (command, arg) for the current screen `mode`.
    Returns None for keys we ignore. See the module docstring for the physical
    button -> KEY_* mapping (SELECT=KEY_MENU, PLAY/PAUSE=KEY_OK on this remote).
    `ok_selects` (set per the active remote profile, see OK_SELECTS_PROFILES)
    makes KEY_OK/KEY_ENTER act as "select" in a list instead of always toggling
    playback -- only for remotes whose center button IS the confirm button."""
    in_list = mode in ("menu", "browse")
    # SELECT button (KEY_MENU): open the menu from now-playing; select in a list.
    if key == "KEY_MENU":
        return ("select", None) if in_list else ("menu", None)
    # HOME button (e.g. the default Xiaomi remote): always jump to the menu.
    if key == "KEY_HOME":
        return ("menu", None)
    # Center OK/ENTER button: PLAY/PAUSE bar on some remotes (always toggles),
    # the confirm/select button on others (see ok_selects docs above).
    if key in ("KEY_OK", "KEY_ENTER"):
        return ("select", None) if (in_list and ok_selects) else ("toggle", None)
    if key in ("KEY_PLAY", "KEY_PAUSE", "KEY_PLAYPAUSE"):
        return ("toggle", None)
    # D-pad LEFT (and the dedicated BACK button, e.g. the default Xiaomi remote):
    # back out of a list; skip to the previous track on now-playing.
    if key in ("KEY_LEFT", "KEY_BACK"):
        return ("back", None) if in_list else ("previous", None)
    if key == "KEY_RIGHT":
        return ("select", None) if in_list else ("next", None)
    # D-pad UP/DOWN: scroll lists (no-op on now-playing).
    if key == "KEY_UP":
        return ("scroll", -1)
    if key == "KEY_DOWN":
        return ("scroll", 1)
    # DAC buttons that still reach the Pi -> open-loop on-screen indicator only.
    if key == "KEY_VOLUMEUP":
        return ("volume", "+")
    if key == "KEY_VOLUMEDOWN":
        return ("volume", "-")
    if key == "KEY_MUTE":
        return ("mute", None)
    # KEY_INPUT / KEY_POWER: DAC-only on this unit -> ignored.
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

    def _ok_selects(self):
        try:
            profile = self._app.settings.get("ir", "profile", default="")
        except Exception:
            return False
        return profile in OK_SELECTS_PROFILES

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
        mapping = command_for(key, self._mode(), ok_selects=self._ok_selects())
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
