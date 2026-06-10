"""Shared CAVA FIFO reader + bar smoother.

ONE implementation, used by the single meter screen -- the old code copy-pasted
_read_fifo into modern/vu/digitalvu. Sable uses its OWN fifos and never the live
plugin's /tmp/cava.fifo or /tmp/display.fifo.

CAVA output format (matches the existing fork's config): method=raw,
data_format=ascii, ascii_max_range=255, bar_delimiter=';' (59),
frame_delimiter='\\n' (10). So one frame is "v;v;v;...;v\\n", each v in 0..255,
`bars` values per frame. We keep only the latest complete frame.
"""
import os

CAVA_FIFO = os.environ.get("SABLE_CAVA_FIFO", "/tmp/sable-cava.fifo")
DISPLAY_FIFO = os.environ.get("SABLE_DISPLAY_FIFO", "/tmp/sable-display.fifo")


class FifoBars:
    def __init__(self, path=DISPLAY_FIFO, bars=24, log=print):
        self.path = path
        self.bars = bars
        self.log = log
        self._fd = None
        self._buf = ""
        self._last = [0.0] * bars

    def _open(self):
        try:
            # O_RDONLY|O_NONBLOCK opens a FIFO immediately even with no writer.
            self._fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            self._fd = None

    def _consume(self, text):
        """Parse buffered ASCII; keep the latest complete frame's bars (0..1)."""
        self._buf += text
        if "\n" not in self._buf:
            return
        *complete, partial = self._buf.split("\n")
        self._buf = partial
        for line in reversed(complete):
            line = line.strip()
            if not line:
                continue
            vals = [int(p) / 255.0 for p in line.split(";") if p.strip().isdigit()]
            if vals:
                self._last = vals
                return

    def read(self):
        """Return the latest bars (list of 0..1); last known value if no new data."""
        if self._fd is None:
            self._open()
        if self._fd is None:
            return self._last
        try:
            data = os.read(self._fd, 65536)
            if data:
                self._consume(data.decode("ascii", "ignore"))
        except (BlockingIOError, OSError):
            pass
        return self._last

    def close(self):
        if self._fd is not None:
            try:
                os.close(self._fd)
            finally:
                self._fd = None


class BarSmoother:
    """Attack/decay smoothing so bars fall gradually. Parameterized, shared --
    not duplicated per screen."""

    def __init__(self, n, attack=0.5, decay=0.08):
        self.n = n
        self.attack = attack
        self.decay = decay
        self.values = [0.0] * n

    def update(self, target):
        out = []
        for i in range(self.n):
            t = target[i] if i < len(target) else 0.0
            v = self.values[i]
            if t > v:
                v = v + (t - v) * self.attack     # rise (smoothed)
            else:
                v = max(t, v - self.decay)         # fall gradually
            self.values[i] = v
            out.append(v)
        return out
