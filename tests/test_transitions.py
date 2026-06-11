"""Self-tests for the transition-robustness fixes (ports from Quadify ModeManager):
spectrum source-gating (Fix 1), FIFO reader liveness (Fix 6), fault-tolerant FSM.go
(Fix 5), stop->clock grace + first-state (Fix 2/4), and the auto-switch cooldown
(Fix 3). Headless -- SimDisplay, no hardware, no cava, no service, no live settings.
Run: python3 tests/test_transitions.py
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from sable import hardware
from sable.app import App, source_feeds_cava
from sable.display.fifo_meter import FifoBars
from sable.display.sim import SimDisplay
from sable.settings import Settings
from sable.state import PlayerState

_FRAMES = os.path.join(tempfile.gettempdir(), "sable_test_frames")


def _app(screen="modern"):
    os.makedirs(_FRAMES, exist_ok=True)
    settings = Settings(path=os.path.join(tempfile.gettempdir(), "sable_test_settings.json"))
    settings.set("display", "screen", screen)
    display = SimDisplay(hardware.OLED.width, hardware.OLED.height, frames_dir=_FRAMES)
    return App(display, settings, dry_run=True, log=lambda *a: None)


def _set(app, **fields):
    """Set player state WITHOUT firing subscribers (no timer / auto-switch side
    effects) -- merges onto the current state, like a partial pushState."""
    app.store._state = app.store._state.merged(fields)


# --- Fix 1: gate spectrum on the audio source -------------------------------

def test_source_feeds_cava_predicate():
    assert source_feeds_cava(PlayerState(service="mpd")) is True
    assert source_feeds_cava(PlayerState(service="")) is True        # unknown -> assume mpd
    assert source_feeds_cava(PlayerState(service="rp2")) is False
    assert source_feeds_cava(PlayerState(service="RP2")) is False        # case-insensitive
    # standard webradio plays through MPD -> it DOES feed cava (gets a spectrum)
    assert source_feeds_cava(PlayerState(service="webradio")) is True
    assert source_feeds_cava(PlayerState(service="spop")) is False
    assert source_feeds_cava(None) is True


def test_nowplaying_gated_by_source():
    app = _app(screen="spectrum")
    _set(app, status="play", service="mpd")
    assert app.nowplaying_screen() == "spectrum"     # MPD feeds cava -> spectrum
    _set(app, status="play", service="rp2")
    assert app.nowplaying_screen() == "modern"       # rp2 bypasses -> modern, not blank meter
    app2 = _app(screen="modern")
    _set(app2, status="play", service="mpd")
    assert app2.nowplaying_screen() == "modern"      # settings=modern -> modern


# --- Fix 1 (cont.) + reconcile ----------------------------------------------

def test_reconcile_clock_to_nowplaying_when_playing():
    app = _app(screen="spectrum")
    app.fsm.go("clock")
    _set(app, status="play", service="mpd")
    app._last_switch_t = 0
    app.reconcile_screen()
    assert app.fsm.current.name == "spectrum"        # missed play edge corrected


def test_reconcile_swaps_spectrum_to_modern_on_source_change():
    app = _app(screen="spectrum")
    app.fsm.go("spectrum")
    _set(app, status="play", service="rp2")          # source now bypasses cava
    app._last_switch_t = 0
    app.reconcile_screen()
    assert app.fsm.current.name == "modern"          # swapped live, no blank meter


def test_reconcile_never_yanks_out_of_menu():
    app = _app(screen="spectrum")
    app.fsm.go("menu")
    _set(app, status="play", service="mpd")
    app._last_switch_t = 0
    app.reconcile_screen()
    assert app.fsm.current.name == "menu"            # navigation is never disturbed


def test_reconcile_noop_when_stopped():
    app = _app(screen="spectrum")
    app.fsm.go("spectrum")
    _set(app, status="stop")
    app._last_switch_t = 0
    app.reconcile_screen()
    assert app.fsm.current.name == "spectrum"        # stop->clock is the timer's job, not reconcile


# --- Fix 3: 0.5s auto-switch cooldown ---------------------------------------

def test_cooldown_blocks_then_converges():
    app = _app(screen="spectrum")
    app.fsm.go("clock")
    _set(app, status="play", service="mpd")
    app._last_switch_t = 0
    app.reconcile_screen()
    assert app.fsm.current.name == "spectrum"        # first switch allowed + stamps cooldown
    _set(app, status="play", service="rp2")          # now wants modern
    app.reconcile_screen()
    assert app.fsm.current.name == "spectrum"        # blocked within the 0.5s window
    app._last_switch_t -= 1.0                         # pretend 1s elapsed
    app.reconcile_screen()
    assert app.fsm.current.name == "modern"          # converges after cooldown


# --- Fix 2 + 4: stop->clock grace + first-state anti-bounce ------------------

def test_first_state_suppresses_stop_fallback():
    app = _app()
    assert app._first_state is True
    app._on_state(PlayerState(status="play"), PlayerState(status="stop"))
    assert app._first_state is False
    assert app._stop_timer is None                   # first stop did NOT arm clock fallback


def test_later_stop_arms_grace_timer():
    app = _app()
    app._first_state = False
    app._on_state(PlayerState(status="play"), PlayerState(status="stop"))
    assert app._stop_timer is not None               # deferred, not immediate
    app._cancel_stop_timer()
    assert app._stop_timer is None


def test_stop_timer_fire_rechecks_status():
    app = _app(screen="spectrum")
    # still stopped at fire time -> fall to clock
    app.fsm.go("spectrum")
    _set(app, status="stop")
    app._last_switch_t = 0
    app._stop_timer_fire()
    assert app.fsm.current.name == "clock"
    # resumed before fire -> stay put (transient stop between tracks)
    app.fsm.go("spectrum")
    _set(app, status="play", service="mpd")
    app._last_switch_t = 0
    app._stop_timer_fire()
    assert app.fsm.current.name == "spectrum"


# --- Fix 5: fault-tolerant FSM.go -------------------------------------------

def test_go_rolls_back_on_failed_on_enter():
    app = _app()
    app.fsm.go("clock")
    menu = app.fsm.screens["menu"]
    original = menu.on_enter
    menu.on_enter = lambda **kw: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        app.fsm.go("menu")                           # on_enter raises -- must not crash
    finally:
        menu.on_enter = original
    assert app.fsm.current.name == "clock"           # rolled back, not stranded on menu


# --- Fix 6: FIFO reader liveness --------------------------------------------

def test_fifo_reopens_when_writer_dies():
    d = tempfile.mkdtemp()
    path = os.path.join(d, "live.fifo")
    os.mkfifo(path)
    fb = FifoBars(path=path, bars=4, log=lambda *a: None, stale_s=0.05)
    opens = {"n": 0}
    real_open = fb._open

    def counting_open():
        opens["n"] += 1
        real_open()

    fb._open = counting_open
    fb.read()                                         # first open (no writer yet)
    assert opens["n"] == 1
    wfd = os.open(path, os.O_WRONLY | os.O_NONBLOCK)  # reader present -> succeeds
    os.write(wfd, b"255;255;255;255\n")
    time.sleep(0.01)
    assert fb.read() == [1.0, 1.0, 1.0, 1.0]          # data flows
    assert opens["n"] == 1                            # no reopen while data flows
    os.close(wfd)                                     # writer dies
    time.sleep(0.08)                                  # exceed stale_s
    fb.read()                                         # stale -> force reopen
    assert opens["n"] == 2
    fb.close()
    os.unlink(path)
    os.rmdir(d)


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for t in tests:
        t()
        print("PASS", t.__name__)
    print("\nAll %d transition tests passed." % len(tests))


if __name__ == "__main__":
    main()
