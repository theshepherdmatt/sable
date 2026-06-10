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
import time

CAVA_FIFO = os.environ.get("SABLE_CAVA_FIFO", "/tmp/sable-cava.fifo")
DISPLAY_FIFO = os.environ.get("SABLE_DISPLAY_FIFO", "/tmp/sable-display.fifo")


class FifoBars:
    def __init__(self, path=DISPLAY_FIFO, bars=24, log=print, stale_s=2.0):
        self.path = path
        self.bars = bars
        self.log = log
        self.stale_s = stale_s         # reopen if no data this long while fd open
        self._fd = None
        self._buf = ""
        self._last = [0.0] * bars
        self._last_data_t = None       # monotonic of the last non-empty read

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
        """Return the latest bars (list of 0..1); last known value if no new data.

        Liveness: reopening used to happen ONLY when self._fd was None, so if CAVA
        respawned or the fifo inode was recreated under a still-open fd, os.read
        returned empty forever and the bars froze. Now we also force a reopen when
        no data has arrived for `stale_s` while the fd is open -- re-resolving the
        path so bars resume within a second or two after a writer restart. (CAVA
        writes frames continuously even on silence, so a quiet passage does NOT
        look stale; only a dead/absent writer does.)"""
        now = time.monotonic()
        if self._fd is None:
            self._open()
            self._last_data_t = now      # start the staleness window on (re)open
        if self._fd is None:
            return self._last
        got = False
        try:
            data = os.read(self._fd, 65536)
            if data:
                self._consume(data.decode("ascii", "ignore"))
                got = True
        except (BlockingIOError, OSError):
            pass
        if got:
            self._last_data_t = now
        elif self._last_data_t is not None and (now - self._last_data_t) >= self.stale_s:
            # Writer likely died / fifo recreated -> reconnect (cheap, O_NONBLOCK).
            self.close()
            self._open()
            self._last_data_t = now
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
