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

# moOde ships peppyalsa (an ALSA plugin, not an MPD fifo+cava like Volumio/quadify)
# -- every audio_output on a stock moOde box already routes through it (see
# /etc/alsa/conf.d/_audioout.conf: pcm._audioout -> slave.pcm "peppy"), so it is
# ALWAYS live once anything plays; Sable never has to spawn anything for it,
# unlike cava. Verified against /opt/peppyspectrum/spectrum.py (moOde's own
# reader): named pipe, PIPE_SIZE = 4 * bars bytes, each bar a little-endian
# uint32, values 0..spectrum_max (100 in moOde's default peppy.conf).
PEPPY_SPECTRUM_FIFO = os.environ.get("SABLE_PEPPY_FIFO", "/tmp/peppyspectrum")
PEPPY_SPECTRUM_MAX = 100


class FifoBars:
    def __init__(self, path=DISPLAY_FIFO, bars=24, log=print, stale_s=2.0,
                 on_stuck=None, stuck_after_s=15.0, stuck_cooldown_s=20.0):
        self.path = path
        self.bars = bars
        self.log = log
        self.stale_s = stale_s         # reopen if no data this long while fd open
        self._fd = None
        self._buf = ""
        self._last = [0.0] * bars
        self._last_data_t = None       # monotonic of the last non-empty read
        # Stuck-writer detection: CAVA can desync internally (observed after MPD
        # player restarts mid-session) and keep running, keep writing well-formed
        # frames on schedule, but with every bar permanently 0 -- the stale_s
        # reopen above does NOT catch this since data IS arriving, just dead.
        # on_stuck() fires once per stuck episode after stuck_after_s of solid
        # zero frames (long enough that a genuine quiet passage should not
        # trigger it); the caller (see app.py's respawn_cava) kills and
        # relaunches the CAVA process.
        self.on_stuck = on_stuck
        self.stuck_after_s = stuck_after_s
        self.stuck_cooldown_s = stuck_cooldown_s
        self._zero_since = None
        self._last_fire_t = None

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
            if any(v > 0.0 for v in self._last):
                self._zero_since = None
                self._last_fire_t = None      # recovered: re-arm immediately
            elif self._zero_since is None:
                self._zero_since = now
        elif self._last_data_t is not None and (now - self._last_data_t) >= self.stale_s:
            # Writer likely died / fifo recreated -> reconnect (cheap, O_NONBLOCK).
            self.close()
            self._open()
            self._last_data_t = now
        # Recovery is RE-ARMABLE, not once-per-session.
        #
        # It used to latch _stuck_fired = True and only clear it on a non-zero
        # bar -- i.e. only if recovery had already worked. On a cold boot it
        # never can: cava is spawned before anything is playing, so the fifo it
        # opens has no writer, the single recovery attempt fires ~15s later
        # while the box is still silent, respawned cava opens a writer-less fifo
        # again, and the one attempt is spent. The spectrum then stays dead for
        # the whole session no matter what you play. Seen exactly once per boot
        # in the journal, always in the first two minutes, always futile.
        #
        # So retry on a cooldown instead of giving up. The cost of a wasted
        # attempt is one process respawn; the cost of not retrying is a dead
        # spectrum until the user restarts the service.
        if (self.on_stuck and self._zero_since is not None
                and now - self._zero_since >= self.stuck_after_s
                and (self._last_fire_t is None
                     or now - self._last_fire_t >= self.stuck_cooldown_s)):
            self._last_fire_t = now
            self.log("fifo_meter: writer stuck (all-zero for %.0fs) -- triggering recovery"
                     % self.stuck_after_s)
            try:
                self.on_stuck()
            except Exception as e:
                self.log("fifo_meter: on_stuck callback error:", e)
        return self._last

    def close(self):
        if self._fd is not None:
            try:
                os.close(self._fd)
            finally:
                self._fd = None


class PeppySpectrumBars:
    """Reads moOde's peppyalsa spectrum pipe directly -- the moOde analogue of
    FifoBars. Binary framing (NOT cava's ASCII): each frame is `bars` little-
    endian uint32s (4 bytes each), values 0..PEPPY_SPECTRUM_MAX. Same
    open/reopen-on-stale idiom as FifoBars since peppyalsa's writer can restart
    independently of Sable (e.g. moOde audio engine restart)."""

    def __init__(self, path=PEPPY_SPECTRUM_FIFO, bars=24, log=print, stale_s=2.0):
        self.path = path
        self.bars = bars
        self.log = log
        self.stale_s = stale_s
        self._fd = None
        self._buf = b""
        self._last = [0.0] * bars
        self._last_data_t = None

    def _open(self):
        try:
            self._fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            self._fd = None

    def _consume(self, data):
        self._buf += data
        frame_bytes = 4 * self.bars
        if len(self._buf) < frame_bytes:
            return
        # Keep only the latest complete frame.
        n_frames = len(self._buf) // frame_bytes
        frame = self._buf[(n_frames - 1) * frame_bytes: n_frames * frame_bytes]
        self._buf = self._buf[n_frames * frame_bytes:]
        vals = []
        for m in range(self.bars):
            v = int.from_bytes(frame[4 * m:4 * m + 4], "little", signed=False)
            vals.append(min(1.0, max(0.0, v / PEPPY_SPECTRUM_MAX)))
        self._last = vals

    def read(self):
        now = time.monotonic()
        if self._fd is None:
            self._open()
            self._last_data_t = now
        if self._fd is None:
            return self._last
        got = False
        try:
            data = os.read(self._fd, 65536)
            if data:
                self._consume(data)
                got = True
        except (BlockingIOError, OSError):
            pass
        if got:
            self._last_data_t = now
        elif self._last_data_t is not None and (now - self._last_data_t) >= self.stale_s:
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


def make_spectrum_reader(bars=24, log=print, on_stuck=None):
    """Platform-aware spectrum source: moOde reads peppyalsa's pipe directly
    (always live, nothing to spawn); Volumio/quadify reads Sable's own cava
    output (see app.py's _start_live_spectrum_source, which spawns cava).
    on_stuck is Volumio/cava-only -- moOde's peppyalsa pipe isn't a spawned
    process Sable owns, so there's nothing to respawn on that path."""
    if os.environ.get("SABLE_PLATFORM", "volumio").strip().lower() == "moode":
        return PeppySpectrumBars(PEPPY_SPECTRUM_FIFO, bars, log=log)
    return FifoBars(DISPLAY_FIFO, bars, log=log, on_stuck=on_stuck)


class BarSmoother:
    """Attack/decay smoothing so bars fall gradually. Parameterized, shared --
    not duplicated per screen."""

    def __init__(self, n, attack=0.5, decay=0.08):
        self.n = n
        self.attack = attack
        self.decay = decay
        self.values = [0.0] * n

    def update(self, target):
        # Track the SOURCE's band count rather than the one we were built with.
        # update() used to iterate range(self.n) and treat anything past it as
        # absent, so a source emitting more bands than the constructor was told
        # about had its top bands silently dropped -- e.g. raising cava's `bars`
        # to 40 against a smoother built for 24 would discard every band above
        # ~8kHz and the meter would look bass-only for no visible reason. The
        # count is a property of the frame, not of this object.
        if target and len(target) != self.n:
            n = len(target)
            old = self.values
            self.values = [old[i] if i < len(old) else 0.0 for i in range(n)]
            self.n = n
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
