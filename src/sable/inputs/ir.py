"""IR remote listener: reads the LIRC daemon socket and maps keys to Sable's ONE
command vocabulary, calling the same app.handle the rotary and IPC use.

Runs in-process on hardware (like the rotary). This is Sable's OWN listener --
it replaces quadify's ir_listener.py, which spoke raw strings to the retired
/tmp/quadify.sock and read mode from /tmp/quadify_mode (both gone). lircd
broadcasts each key to every connected client, so this coexists with any other
listener without stealing events.

Volume/mute are deliberately NOT mapped: this unit's volume is the EVO Sabre
DAC's own (Volumio mixer_type=None), and the rotary contract forbids volume.
"""
import os
import socket
import threading
import time

_LIRC_SOCKETS = ("/run/lirc/lircd", "/var/run/lirc/lircd")

# IR key name -> (sable command, arg). Unmapped keys are ignored.
_KEYMAP = {
    "KEY_OK":         ("select", None),
    "KEY_ENTER":      ("select", None),
    "KEY_MENU":       ("menu", None),
    "KEY_HOME":       ("home", None),
    "KEY_BACK":       ("back", None),
    "KEY_EXIT":       ("back", None),
    "KEY_RETURN":     ("back", None),
    "KEY_UP":         ("scroll", -1),
    "KEY_DOWN":       ("scroll", 1),
    # D-pad convention for remotes without a BACK key (e.g. ApEvo):
    # LEFT exits / goes up a level, RIGHT enters / selects.
    "KEY_LEFT":       ("back", None),
    "KEY_RIGHT":      ("select", None),
    "KEY_NEXT":       ("next", None),
    "KEY_PREVIOUS":   ("previous", None),
    "KEY_PLAY":       ("toggle", None),
    "KEY_PAUSE":      ("toggle", None),
    "KEY_PLAYPAUSE":  ("toggle", None),
    "KEY_INPUT":      ("dac_input", None),
}


class IrListener(threading.Thread):
    def __init__(self, handle, debounce_s=0.12, log=print):
        super().__init__(daemon=True, name="sable-ir")
        self.handle = handle
        self.debounce_s = debounce_s
        self.log = log
        self._running = True
        self._last = {}

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
        try:
            repeat = int(parts[1], 16)
        except ValueError:
            repeat = 0
        mapping = _KEYMAP.get(parts[2])
        if mapping is None:
            return
        cmd, arg = mapping
        # Allow auto-repeat only for scroll (hold-to-scroll); debounce the rest.
        if repeat > 0 and cmd != "scroll":
            return
        now = time.monotonic()
        if now - self._last.get(parts[2], 0.0) < self.debounce_s:
            return
        self._last[parts[2]] = now
        try:
            self.handle(cmd, arg)
        except Exception as e:
            self.log("ir: handle error", cmd, e)

    def stop(self):
        self._running = False
