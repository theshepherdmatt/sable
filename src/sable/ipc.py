"""The ONE IPC channel: a newline-delimited JSON command socket.

The display app is the server. Clients (IR listener, controls daemon, the Node
plugin on settings-save, and the sablectl debug CLI) send one JSON object per
line: {"cmd": "scroll", "arg": -1}. This replaces the old mix of config-file
writes, blind systemctl restart, and two parallel eventing systems.
"""
import json
import os
import socket
import threading

SOCKET_PATH = os.environ.get("SABLE_SOCK", "/tmp/sable-cmd.sock")


class CommandServer(threading.Thread):
    def __init__(self, handler, path=SOCKET_PATH, log=None):
        super().__init__(daemon=True, name="sable-ipc")
        self.handler = handler
        self.path = path
        self.log = log or (lambda *a: None)
        self._sock = None
        self._running = True

    def run(self):
        try:
            os.unlink(self.path)
        except FileNotFoundError:
            pass
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock.bind(self.path)
        os.chmod(self.path, 0o666)
        self._sock.listen(8)
        self.log("ipc: listening on", self.path)
        while self._running:
            try:
                conn, _ = self._sock.accept()
            except OSError:
                break
            with conn:
                data = conn.recv(4096).decode("utf-8", "replace")
            for line in data.splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    msg = json.loads(line)
                    self.handler(msg.get("cmd", ""), msg.get("arg"))
                except Exception as exc:
                    self.log("ipc: bad command", repr(line), exc)

    def stop(self):
        self._running = False
        try:
            if self._sock:
                self._sock.close()
        finally:
            try:
                os.unlink(self.path)
            except FileNotFoundError:
                pass


def send(cmd, arg=None, path=SOCKET_PATH):
    """Fire-and-forget a single command to the running app."""
    payload = (json.dumps({"cmd": cmd, "arg": arg}) + "\n").encode("utf-8")
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
        s.connect(path)
        s.sendall(payload)
