"""NTP boot gate for an RTC-less Pi.

The Pi has no battery clock, so at boot the time is wrong until ntpsec steps it.
We hold the splash until the clock is trustworthy, so the clock screen never
shows a bogus time.

Primary signal: ntpq -c "rv 0 stratum" reporting stratum 1..15. On ntpsec this
flips noticeably BEFORE the kernel NTPSynchronized flag, so it is the fastest
reliable gate. Fallbacks: timedatectl NTPSynchronized, then a year>=2024 floor.
"""
import subprocess
import time


def _ntpq_synced():
    try:
        out = subprocess.run(
            ["ntpq", "-c", "rv 0 stratum"],
            capture_output=True, text=True, timeout=3,
        ).stdout
    except Exception:
        return None
    for tok in out.replace(",", " ").split():
        if tok.startswith("stratum="):
            try:
                st = int(tok.split("=")[1])
            except ValueError:
                return None
            return 1 <= st <= 15
    return None


def _timedatectl_synced():
    try:
        out = subprocess.run(
            ["timedatectl", "show", "-p", "NTPSynchronized", "--value"],
            capture_output=True, text=True, timeout=3,
        ).stdout.strip()
        return out == "yes"
    except Exception:
        return None


def is_synced():
    for probe in (_ntpq_synced, _timedatectl_synced):
        if probe() is True:
            return True
    return time.localtime().tm_year >= 2024


def wait_for_clock(timeout=45.0, poll=0.5, on_wait=None):
    """Block until the clock is trustworthy or timeout elapses.

    Returns True if synced, False if it gave up. on_wait() fires once when the
    first poll is not yet synced (e.g. to update the splash subtitle).
    """
    deadline = time.monotonic() + timeout
    notified = False
    while True:
        if is_synced():
            return True
        if not notified and on_wait:
            on_wait()
            notified = True
        if time.monotonic() >= deadline:
            return False
        time.sleep(poll)
