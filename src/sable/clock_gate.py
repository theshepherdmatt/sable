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
    """True once ntpd has actually DISCIPLINED the clock, not merely picked a
    candidate server.

    Stratum is not that signal, though it looks like it. ntpd publishes the
    stratum of the server it has SELECTED long before it has stepped the clock,
    so `rv 0 stratum` reports e.g. stratum=2 within a second of boot while the
    system time is still whatever fake-hwclock restored. Measured on this box:
    stratum=2 alongside `no_sys_peer` and a clock over an hour wrong. Gating on
    it dropped the splash after ~1s, showed a bogus time, then jumped when ntpd
    finally stepped -- exactly the behaviour this gate exists to prevent.

    The real signal is in the status flags of a full `rv 0`:
      sync_unspec / no_sys_peer -> no system peer chosen yet, clock NOT ruled
      leap_alarm                -> ntpd itself declaring itself unsynchronised
      sync_ntp                  -> disciplined by NTP; the time can be trusted
    """
    try:
        out = subprocess.run(
            ["ntpq", "-c", "rv 0"],
            capture_output=True, text=True, timeout=3,
        ).stdout
    except Exception:
        return None
    if not out.strip():
        return None
    if "leap_alarm" in out:
        return False
    if "sync_ntp" in out:
        return True

    # No system peer selected yet -- but that is NOT the same as a wrong clock,
    # and waiting for it would hold the splash far too long. Measured on this
    # box: ntpd STEPPED the clock ~40s after boot, then took several more
    # MINUTES to promote a peer to sys_peer. The user-visible truth ("the time
    # on screen is right") happens at the step, not at the promotion.
    #
    # So fall back to what ntpd actually measured: a peer we have heard from
    # (reach != 0) plus a small system offset means the clock is already
    # correct. 128ms is ntpd's own step threshold -- below it ntpd slews rather
    # than steps, i.e. it considers the clock right. Before the step the offset
    # is huge (or reach is still 0), so this stays False exactly as long as it
    # should.
    offset_ms = None
    for tok in out.replace(",", " ").split():
        if tok.startswith("offset="):
            try:
                offset_ms = abs(float(tok.split("=", 1)[1]))
            except ValueError:
                offset_ms = None
            break
    if offset_ms is None:
        return None
    if not _ntpq_has_reachable_peer():
        # reach == 0 everywhere: ntpd has no measurement at all yet, and the
        # offset it reports is a meaningless 0.000 that would otherwise look
        # like a perfectly synced clock.
        return False
    return offset_ms < 128.0


def _ntpq_has_reachable_peer():
    """True if ntpd has actually heard back from at least one peer.

    `reach` is an 8-bit shift register of the last 8 polls; 0 means nothing has
    ever been received, so no measurement exists to judge the clock by.
    """
    try:
        out = subprocess.run(["ntpq", "-pn"], capture_output=True, text=True,
                             timeout=3).stdout
    except Exception:
        return False
    for line in out.splitlines()[2:]:          # skip header + separator
        cols = line.split()
        if len(cols) >= 7 and cols[0].startswith((".", "0", "1", "2", "3", "4",
                                                  "5", "6", "7", "8", "9",
                                                  "*", "+", "-", "#", "x", " ")):
            try:
                if int(cols[6]) != 0:
                    return True
            except ValueError:
                continue
    return False


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
    """ntpq is AUTHORITATIVE when it answers; the others are positive-only.

    The old logic was `any(True) -> True`, so a single premature positive beat a
    correct negative -- which is how a stratum reading defeated timedatectl's
    "no" and dropped the splash on a wrong clock.

    Note timedatectl cannot be trusted in the negative direction here: on this
    box NTPSynchronized reads "no" even long after ntpd has the clock right
    (systemd only sets that flag for its own timesyncd, which is inactive --
    ntpsec is the daemon in use). Treating its "no" as authoritative would hold
    the splash up until the timeout on every single boot.
    """
    ntpq = _ntpq_synced()
    if ntpq is not None:
        return ntpq
    if _timedatectl_synced() is True:
        return True
    # Last resort only: fake-hwclock satisfies a year floor instantly at boot,
    # so this can confirm a plausible clock but must never override a probe.
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
