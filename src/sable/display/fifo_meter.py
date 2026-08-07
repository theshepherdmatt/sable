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

# moOde ships peppyalsa (an ALSA plugin, not an MPD fifo+cava like Volumio/quadify).
# Sable never has to spawn anything for it, unlike cava -- but it is NOT on by
# default: moOde ships the config as peppy.conf.HIDE and its worker renames it to
# peppy.conf only once the user enables peppyalsa in moOde's own UI (verified on
# moOde 10; Quoode's installer tells its users the same thing). Until then this
# pipe exists but nothing ever writes to it. Once enabled, _audioout routes
# through it (pcm._audioout -> slave.pcm "peppy") and it is live whenever
# anything plays. Verified against /opt/peppyspectrum/spectrum.py (moOde's own
# reader): named pipe, PIPE_SIZE = 4 * bars bytes, each bar a little-endian
# uint32, values 0..spectrum_max (100 in moOde's default peppy.conf).
PEPPY_SPECTRUM_FIFO = os.environ.get("SABLE_PEPPY_FIFO", "/tmp/peppyspectrum")
PEPPY_SPECTRUM_MAX = 100
# peppyalsa's own config, which is what actually decides the wire format.
PEPPY_CONF = os.environ.get("SABLE_PEPPY_CONF", "/etc/alsa/conf.d/peppy.conf")
# Bars per frame that peppyalsa WRITES. This is NOT the number of bars Sable
# draws: moOde ships spectrum_size 30 while the screens ask for 24, and treating
# the display's count as the frame size mis-slices a 120-byte frame into 96-byte
# windows that then drift across frame boundaries -- the bars stay plausible
# (they are all mid-range) but no longer correspond to their frequencies, which
# reads as a flat, dead wall. Measured on moOde 10: frames arrive as 120/240/360
# bytes, i.e. multiples of 30 * 4.
PEPPY_SPECTRUM_BARS = 30


def _peppy_conf_int(key, default):
    """Read an integer from peppyalsa's pcm_scope block, e.g. `spectrum_size 30`.

    Prefer the writer's OWN config over a constant here: these are moOde's to
    change (its peppy-config page edits them), and a mismatch is silent -- the
    pipe carries no framing or header to notice it with.
    """
    env = os.environ.get("SABLE_PEPPY_" + key.upper())
    if env:
        try:
            return int(env)
        except ValueError:
            pass
    try:
        with open(PEPPY_CONF, "r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                parts = line.strip().split()
                if len(parts) >= 2 and parts[0] == key:
                    return int(parts[1])
    except (OSError, ValueError):
        pass
    return default


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

    def __init__(self, path=PEPPY_SPECTRUM_FIFO, bars=24, log=print, stale_s=2.0,
                 src_bars=None, vmax=None):
        self.path = path
        self.bars = bars                  # bars Sable DRAWS
        # Bars peppyalsa WRITES -- a different number, read from its own config.
        self.src_bars = int(src_bars or _peppy_conf_int("spectrum_size",
                                                        PEPPY_SPECTRUM_BARS))
        self.vmax = int(vmax or _peppy_conf_int("spectrum_max",
                                                PEPPY_SPECTRUM_MAX)) or 100
        self.log = log
        self.stale_s = stale_s
        # Auto-gain, the thing cava does for Volumio and peppyalsa does NOT.
        # peppyalsa reports against a fixed spectrum_max (100) that real music
        # never approaches -- measured on moOde 10, a busy rock track peaks at
        # ~58 and sits around 15 -- so every bar is squashed into the bottom
        # half of the panel and the spectrum reads as a flat, mid-height wall.
        # cava instead tracks the recent peak and normalises to it, which is why
        # the same music looks alive on Volumio. Track a decaying running peak
        # and divide by it. agc_floor caps the gain (~1/0.12 = 8x) so silence and
        # quiet passages are not amplified into noise.
        # Tracked at BOTH ends. Scaling by the peak alone is not enough: with
        # logarithmic_amplitude on, peppyalsa keeps a whole frame inside roughly
        # a 1.6:1 range (measured: bins 39..63), so dividing by the peak just
        # moves the wall from mid-panel to the top -- every bar at 0.74..1.00.
        # Stretching between a decaying floor and a decaying peak is what
        # restores the separation between loud and quiet bins.
        self.agc_decay = 0.995      # per frame; ~3s half-life at peppy's ~46fps
        self.agc_min_span = 0.10    # never stretch a near-flat frame to full scale
        self._agc_hi = self.agc_min_span
        self._agc_lo = 0.0
        self._fd = None
        self._buf = b""
        self._last = [0.0] * bars
        self._last_data_t = None

    def _open(self):
        try:
            self._fd = os.open(self.path, os.O_RDONLY | os.O_NONBLOCK)
        except OSError:
            self._fd = None
            return
        # Drain whatever is already sitting in the pipe before reading for real.
        #
        # The frames carry NO delimiter or header, so alignment is positional and
        # there is no way to recover it from content. peppyalsa's writes are one
        # atomic 120-byte frame each, so a reader that starts on a write boundary
        # stays aligned -- but a pipe left holding a PARTIAL frame (a previous
        # reader stopped mid-frame) puts us permanently half a frame out, and the
        # leftover-buffer logic in _consume() then preserves that offset forever.
        # Every displayed frame straddles two real ones: values stay plausible,
        # bars stop matching their frequencies, and the result looks glitchy and
        # off the beat. Observed for real: reads of 12/16/84/108 bytes against a
        # 120-byte frame. Discarding the backlog costs one frame (~20ms).
        self._buf = b""
        while True:
            try:
                if not os.read(self._fd, 65536):
                    break
            except (BlockingIOError, OSError):
                break

    def _consume(self, data):
        self._buf += data
        # Frame size follows the WRITER, not the display -- see PEPPY_SPECTRUM_BARS.
        frame_bytes = 4 * self.src_bars
        if len(self._buf) < frame_bytes:
            return
        # Keep only the LATEST complete frame -- the instantaneous spectrum.
        #
        # peppyalsa writes ~46fps against Sable's 20fps render, so most frames are
        # discarded, and it is tempting to peak-hold across the burst instead so
        # no transient is missed. Don't: max-ing 2-3 consecutive frames pulls
        # every bin up towards the local maximum, which SHRINKS the difference
        # between bins and makes the wall flatter -- the exact complaint. Quoode,
        # which is known good on moOde, also takes the latest frame and leaves
        # the ballistics to the smoother downstream.
        n_frames = len(self._buf) // frame_bytes
        frame = self._buf[(n_frames - 1) * frame_bytes: n_frames * frame_bytes]
        self._buf = self._buf[n_frames * frame_bytes:]
        src = []
        for m in range(self.src_bars):
            v = int.from_bytes(frame[4 * m:4 * m + 4], "little", signed=False)
            src.append(min(1.0, max(0.0, v / self.vmax)))
        # Auto-gain: chase each end fast in its own direction, release slowly, so
        # the loudest bin reaches the top of the panel and the quietest reaches
        # the bottom instead of every bar sitting in a band in between.
        hi, lo = max(src), min(src)
        self._agc_hi = max(hi, self._agc_hi - (self._agc_hi - hi) * (1 - self.agc_decay))
        self._agc_lo = min(lo, self._agc_lo + (lo - self._agc_lo) * (1 - self.agc_decay))
        span = max(self._agc_hi - self._agc_lo, self.agc_min_span)
        src = [min(1.0, max(0.0, (v - self._agc_lo) / span)) for v in src]
        self._last = self._resample(src)

    def _resample(self, src):
        """Map the writer's bars onto the display's, by PEAK within each bucket.

        Peak rather than mean: these are spectrum magnitudes, and averaging
        neighbouring bins is exactly what makes a busy spectrum look flat --
        the loud bin gets diluted by its quiet neighbour. Peak keeps the
        transient that makes the bar visibly move.
        """
        n_out, n_in = self.bars, len(src)
        if n_out == n_in or not n_in:
            return list(src)
        out = []
        for m in range(n_out):
            lo = (m * n_in) // n_out
            hi = max(lo + 1, ((m + 1) * n_in) // n_out)
            out.append(max(src[lo:hi]))
        return out

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
