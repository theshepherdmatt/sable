"""Self-tests for cava stuck-writer recovery (FifoBars.on_stuck).

The bug these pin: recovery used to latch after ONE attempt and only re-arm on
a non-zero bar -- i.e. only if it had already worked. On a cold boot it cannot:
cava is spawned before anything plays, so it opens /tmp/cava.fifo with no
writer and stays silent; the single attempt fires ~15s in while the box is
still quiet, the respawned cava opens a writer-less fifo again, and that is the
only attempt the session ever gets. The spectrum then stays dead until the
service is restarted -- observed once per boot in the journal, always futile.

POSIX only: FifoBars uses os.O_NONBLOCK. Run on the Pi:
    ssh volumio@<pi> 'cd /home/volumio/sable && python3 -m pytest tests/test_cava_recovery.py -q'
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sable.display.fifo_meter import FifoBars

# pytest is not installed on the Pi, and these tests only run there (Windows has
# no os.O_NONBLOCK / mkfifo), so the pytest import is optional -- the file also
# runs standalone, like tests/test_transitions.py.
try:
    import pytest
    pytestmark = pytest.mark.skipif(not hasattr(os, "O_NONBLOCK"),
                                    reason="POSIX fifos only")
except ImportError:  # pragma: no cover - the Pi
    pytest = None

BARS = 4
ZERO_FRAME = (";".join(["0"] * BARS) + "\n").encode()
LIVE_FRAME = (";".join(["200"] * BARS) + "\n").encode()


def _fifo():
    path = os.path.join(tempfile.mkdtemp(), "sable-test.fifo")
    os.mkfifo(path)
    return path


def _pump(reader, wfd, frame, seconds, step=0.02):
    """Feed `frame` at ~50Hz and read, the way the render tick does."""
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        try:
            os.write(wfd, frame)
        except OSError:
            pass
        reader.read()
        time.sleep(step)


def test_recovery_retries_while_the_writer_stays_stuck():
    """The cold-boot case: cava keeps emitting zeros. Recovery must keep
    trying, not give up after one go."""
    path = _fifo()
    fires = []
    r = FifoBars(path, bars=BARS, log=lambda *a: None,
                 on_stuck=lambda: fires.append(time.monotonic()),
                 stuck_after_s=0.2, stuck_cooldown_s=0.3)
    r.read()                                    # opens the fifo
    wfd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
    try:
        _pump(r, wfd, ZERO_FRAME, 1.6)
    finally:
        os.close(wfd)
        r.close()
    assert len(fires) >= 3, (
        "recovery fired %d time(s) in 1.6s with a 0.3s cooldown -- it is "
        "latching after the first attempt again" % len(fires))


def test_recovery_rearms_after_real_audio_returns():
    """Non-zero bars mean cava is healthy: clear the stuck state so a LATER
    stall gets a fresh attempt straight away rather than waiting out a
    cooldown."""
    path = _fifo()
    fires = []
    r = FifoBars(path, bars=BARS, log=lambda *a: None,
                 on_stuck=lambda: fires.append(time.monotonic()),
                 stuck_after_s=0.2, stuck_cooldown_s=5.0)
    r.read()
    wfd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
    try:
        _pump(r, wfd, ZERO_FRAME, 0.5)          # stall -> one fire
        first = len(fires)
        assert first >= 1
        _pump(r, wfd, LIVE_FRAME, 0.3)          # audio returns -> re-arm
        assert r._zero_since is None
        assert r._last_fire_t is None, "healthy data did not clear the latch"
        _pump(r, wfd, ZERO_FRAME, 0.5)          # stall again
        assert len(fires) > first, (
            "a fresh stall did not fire; the 5s cooldown was not reset by "
            "healthy audio")
    finally:
        os.close(wfd)
        r.close()


def test_healthy_writer_never_triggers_recovery():
    """A working cava must not be respawned under any circumstances."""
    path = _fifo()
    fires = []
    r = FifoBars(path, bars=BARS, log=lambda *a: None,
                 on_stuck=lambda: fires.append(1),
                 stuck_after_s=0.2, stuck_cooldown_s=0.3)
    r.read()
    wfd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)
    try:
        _pump(r, wfd, LIVE_FRAME, 1.0)
    finally:
        os.close(wfd)
        r.close()
    assert fires == []


if __name__ == "__main__":
    failed = 0
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
                print("PASS", _name)
            except AssertionError as exc:
                failed += 1
                print("FAIL", _name, "->", exc)
    print("%d failure(s)" % failed)
    sys.exit(1 if failed else 0)
