"""Sable display app -- wiring + verification harnesses.

Boot: splash -> Volumio-ready wait (stubbed in the slice) -> NTP clock gate ->
clock (or now-playing if already playing). One command handler serves every input
source through the same vocabulary; one StateStore drives every screen.

Run modes:
  --sim --demo        Phase 0 scripted boot+input run, ASCII previews
  --modern-fixture    render the modern screen from the real RP capture (PNGs)
  --modern-live       connect read-only to Volumio, render modern live (PNGs)
  --sim               interactive: IPC socket + stdin, SimDisplay
  --hardware          REAL SSD1322 over SPI. Refused while quadify.service is
                      active (would contend for SPI/GPIO); foreground + Ctrl-C
                      clean. --stage clock|modern|spectrum|full brings it up
                      incrementally.
"""
import argparse
import json
import os
import sys
import threading
import time

from . import clock_gate, hardware
from .display.albumart import AlbumArtCache
from .display.fonts import Fonts
from .display.icons import IconCache
from .display.sim import SimDisplay
from .fsm import FSM
from .screens.clock import ClockScreen
from .screens.menu import MenuScreen
from .screens.meter import MeterScreen
from .screens.modern import ModernScreen
from .screens.browse import BrowseScreen
from .screens.splash import SplashScreen
from .settings import Settings
from .state import StateStore

TRANSPORT = {"play", "pause", "toggle", "next", "previous", "random", "repeat"}
_FIXTURE = os.path.normpath(
    os.path.join(os.path.dirname(__file__), "..", "..", "tests", "fixtures", "rp_paused.json")
)


def status_event(old, new):
    """Map a status change to an FSM event: active(play|pause) <-> stopped."""
    was = old.status in ("play", "pause")
    now = new.status in ("play", "pause")
    if now and not was:
        return "play"
    if was and not now:
        return "stop"
    return None


# Sources whose audio does NOT pass through MPD's PCM fifo, so CAVA sees nothing
# and a spectrum/meter screen would be blank (rp2/streaming via mpv, web radio,
# AirPlay via shairport). MPD / local-library DOES feed CAVA. Identified by the
# Volumio `service` value. ONE gate -- keep the set here, not scattered as
# service== checks across the resolution paths.
_NON_CAVA_SERVICES = frozenset({
    "rp2", "radioparadise", "radio_paradise", "motherearthradio",
    "webradio", "airplay", "airplay_emulation", "spop", "spotify",
})


def source_feeds_cava(state):
    """True only when the current audio is MPD-routed (so it reaches the CAVA
    fifo). Non-MPD sources bypass it -> the spectrum would be blank, so callers
    fall back to the modern screen. Unknown/empty service -> assume MPD-routed
    (don't suppress a source we don't recognise)."""
    if state is None:
        return True
    return (state.service or "").strip().lower() not in _NON_CAVA_SERVICES


class App:
    def __init__(self, display, settings, dry_run=True, log=print):
        self.display = display
        self.settings = settings
        self.fonts = Fonts()
        self.store = StateStore(log=log)
        self.albumart = AlbumArtCache(size=(ModernScreen.ART, ModernScreen.ART), log=log)
        self.icons = IconCache(log=log)
        self.dry_run = dry_run
        self.log = log
        self._render_lock = threading.Lock()
        self.listener = None   # set on hardware/live runs; BrowseScreen uses it
        screens = [SplashScreen(self), ClockScreen(self), MenuScreen(self),
                   ModernScreen(self), MeterScreen(self), BrowseScreen(self)]
        self.fsm = FSM(self, screens, log=log)
        self.store.subscribe(self._on_state)
        self.last_input = time.monotonic()
        self.asleep = False
        self._stop_timer = None        # deferred stop->clock fall-back (Fix 2)
        self._stop_grace_s = 1.5
        self._last_switch_t = 0.0      # auto-transition cooldown (Fix 3)
        self._switch_cooldown_s = 0.5
        self._first_state = True       # suppress stale-stop bounce on boot (Fix 4)

    def nowplaying_screen(self):
        """Resolve the now-playing screen for the FSM @nowplaying token. A spectrum
        style only resolves to the meter when (a) the current source actually feeds
        CAVA (source_feeds_cava) -- else the fifo is empty and the meter would be
        blank -- AND (b) we are actually PLAYING: a paused spectrum is just flat
        bars (the old 'dead blank panel' on pause), so paused/stopped falls to the
        modern hero, which owns the DESIGNED paused state. This is the ONE place the
        gate is applied; base_screen() and reconcile_screen() both delegate here."""
        s = self.settings.get("display", "screen", default="modern")
        st = self.store.get()
        if (s in ("spectrum", "vu", "digitalvu", "bars", "dots")
                and st.status == "play"
                and source_feeds_cava(st)):
            return "spectrum"
        return "modern"

    def spectrum_available(self):
        """True when the current source feeds CAVA, so the now-playing hero's dim
        spectrum floor has real bars to draw (vs a flat/empty fifo)."""
        return source_feeds_cava(self.store.get())

    def base_screen(self):
        """The screen to show when NOT in a menu/browse overlay: now-playing while
        active, the clock when stopped. Used for menu/browse exits."""
        return self.nowplaying_screen() if self.store.get().status in ("play", "pause") else "clock"

    def reconcile_screen(self):
        """Backstop for the edge-triggered screen switch. Run every render tick while
        playing; corrects two things and never touches menu/browse:
          (a) parked on the clock while audio plays (a 'play' edge dropped because we
              were in a menu/browse/splash) -> go to now-playing; and
          (b) the resolved now-playing screen changed while still playing -- e.g. the
              source switched MPD<->rp2, so spectrum<->modern -- with no clock flash."""
        if self.asleep or self.fsm.current is None:
            return
        if self.store.get().status not in ("play", "pause"):
            return
        cur = self.fsm.current.name
        want = self.nowplaying_screen()
        if cur == "clock" or (cur in ("modern", "spectrum") and cur != want):
            if self._switch_due():
                self._stamp_switch()
                self.go(want)

    # --- state -> FSM + redraw ---
    def _on_state(self, old, new):
        ev = status_event(old, new)
        first, self._first_state = self._first_state, False
        if ev == "play":
            self._cancel_stop_timer()
            if self._switch_due():
                self._stamp_switch()
                self.fsm.dispatch("play")
        elif ev == "stop":
            if first:
                # First pushState after boot/handoff is often a stale stop from the
                # previous session -- don't fall to the clock on it (Fix 4).
                self.render()
            else:
                # Defer the clock fall-back: a transient stop between tracks/sources
                # must not flash the clock (Fix 2).
                self._arm_stop_timer()
        else:
            self.render()

    # --- deferred stop -> clock (Fix 2: 1.5s grace, non-stacking) ---
    def _arm_stop_timer(self):
        self._cancel_stop_timer()
        self._stop_timer = threading.Timer(self._stop_grace_s, self._stop_timer_fire)
        self._stop_timer.daemon = True
        self._stop_timer.start()

    def _cancel_stop_timer(self):
        if self._stop_timer is not None:
            self._stop_timer.cancel()
            self._stop_timer = None

    def _stop_timer_fire(self):
        # Re-read status at fire time: only fall to clock if STILL stopped/paused,
        # so a stop that resolves back to play between tracks never reaches clock.
        self._stop_timer = None
        if self.store.get().status not in ("play", "pause"):
            self._stamp_switch()
            self.fsm.dispatch("stop")

    # --- auto-transition cooldown (Fix 3) ---
    def _switch_due(self):
        """True if enough time has passed since the last AUTOMATIC screen switch.
        Throttles playback/source-driven switches (play edge, reconcile, stop) so a
        pushState burst can't flicker; user navigation (menu/back/select via go) is
        never gated. reconcile retries each tick, so it still converges after a burst."""
        return (time.monotonic() - self._last_switch_t) >= self._switch_cooldown_s

    def _stamp_switch(self):
        self._last_switch_t = time.monotonic()

    # --- idle / OLED sleep (burn-in protection) ---
    def note_activity(self):
        """Any real input (rotary/IR/IPC) or resumed playback: reset the idle
        timer and wake the panel if it had slept."""
        self.last_input = time.monotonic()
        if self.asleep:
            self.asleep = False
            try:
                self.display.wake()
            except Exception:
                pass
            self.log("screensaver: OLED wake")
            self.render()

    def tick_idle(self, now):
        """Called each render tick. Moving content (playing) or an open menu
        counts as activity; otherwise after idle_s of stillness the panel sleeps
        (0xAE). idle_s == 0 disables sleep."""
        active = self.store.get().status == "play" or self.fsm.current.name == "menu"
        if active:
            self.note_activity()
            return
        idle_s = self.settings.get("screensaver", "idle_s", default=3600)
        if not self.asleep and idle_s and (now - self.last_input) >= idle_s:
            self.asleep = True
            try:
                self.display.sleep()
            except Exception:
                pass
            self.log("screensaver: OLED sleep (idle %ss)" % idle_s)

    # --- rendering ---
    def render(self):
        if self.asleep:
            return
        from PIL import ImageDraw
        with self._render_lock:
            img = self.display.blank_canvas()
            draw = ImageDraw.Draw(img)
            if self.fsm.current is not None:
                self.fsm.current.render(img, draw, self.display.width, self.display.height)
            self.display.present(img)

    def go(self, name, **kwargs):
        self.fsm.go(name, **kwargs)

    # --- the one command vocabulary ---
    def handle(self, cmd, arg=None):
        self.note_activity()
        cur = self.fsm.current
        if cmd == "scroll":
            cur.handle_scroll(int(arg or 0))
            self.render()
        elif cmd == "select":
            cur.handle_select()
            self.render()
        elif cmd == "back":
            cur.handle_back()
            self.render()
        elif cmd == "menu":
            self.fsm.dispatch("menu")
        elif cmd == "home":
            self.fsm.dispatch("back")
        elif cmd in TRANSPORT:
            self._transport(cmd)
        elif cmd == "dac_input":
            self._dac_input()
        elif cmd == "reload_config":
            self.settings.load()
            self.log("config reloaded")
            self.render()
        elif cmd == "shutdown":
            self.log("[dry-run] would poweroff" if self.dry_run else "poweroff")
        else:
            self.log("unknown cmd:", cmd)

    def _transport(self, cmd):
        if self.dry_run:
            self.log("[dry-run] volumio", cmd)
        else:
            import subprocess
            subprocess.run(["volumio", cmd], check=False)

    def _dac_input(self):
        idx = (self.settings.get("dac", "input_index", default=0) + 1) % len(hardware.DAC_INPUTS)
        self.settings.set("dac", "input_index", idx)
        self.log("DAC input ->", hardware.DAC_INPUTS[idx], "(hint; user-correctable)")

    # --- Phase 0 scripted slice ---
    def run_demo(self):
        self.log("=== Sable Phase 0 vertical slice ===")
        self.go("splash")
        splash = self.fsm.screens["splash"]

        def waiting():
            splash.subtitle = "waiting for clock"
            self.render()

        synced = clock_gate.wait_for_clock(timeout=45.0, on_wait=waiting)
        self.log("clock trustworthy:", synced)
        self.go("clock")

        from .inputs.sim_input import run_script
        run_script(self.handle, [
            ("menu", None), ("scroll", 1), ("scroll", 1), ("scroll", -1), ("select", None),
        ], log=self.log)
        run_script(self.handle, [("play", None), ("dac_input", None)], log=self.log)
        self.log("=== slice complete ===")


def build_sim_app(frames_dir="var/frames"):
    settings = Settings()
    display = SimDisplay(hardware.OLED.width, hardware.OLED.height, frames_dir=frames_dir)
    return App(display, settings, dry_run=True)


def _load_fixture():
    with open(_FIXTURE, "r", encoding="utf-8") as fh:
        return json.load(fh)


def run_modern_fixture(frames_dir="var/frames", frames=6, interval=1.0):
    """Render the modern screen from the real RP capture, then flip to a 'playing'
    variant and render several frames so the marquee scrolls and the seek bar
    advances. Album art starts as a placeholder and fills in if the fetch lands."""
    app = build_sim_app(frames_dir=frames_dir)
    app.go("clock")
    fx = _load_fixture()
    app.log("=== modern: paused fixture (status=%s) ===" % fx.get("status"))
    app.store.apply_pushstate(fx)        # status pause -> dispatch play -> modern
    app.log("current screen:", app.fsm.current.name)

    app.log("=== modern: playing, %d frames @ %.1fs (marquee + seek advance) ==="
            % (frames, interval))
    playing = dict(fx, status="play")
    app.store.apply_pushstate(playing)
    for i in range(frames):
        time.sleep(interval)
        app.render()
    app.log("=== final state -> screen:", app.fsm.current.name,
            "progress=%.2f" % app.store.progress_fraction())


def run_modern_live(seconds=12, interval=2.0, frames_dir="var/frames"):
    from .volumio.listener import VolumioListener
    app = build_sim_app(frames_dir=frames_dir)
    app.go("clock")
    listener = VolumioListener(app.store, log=app.log)
    listener.start()
    time.sleep(2.0)
    st = app.store.get()
    app.log("=== live state -> screen ===")
    app.log("status=%s screen=%s title=%r artist=%r vol=%d"
            % (st.status, app.fsm.current.name, st.title, st.artist, st.volume))
    n = max(1, int(seconds / interval))
    for i in range(n):
        time.sleep(interval)
        app.render()
    listener.stop()
    app.log("=== live render complete ===")


def run_spectrum_fixture(frames_dir="var/frames", sweep=18):
    """Pure render: feed the meter SYNTHETIC bars (a moving band, then zeros) so
    bar rendering AND attack/decay smoothing are proven without CAVA or a fifo."""
    app = build_sim_app(frames_dir=frames_dir)
    app.settings.set("display", "screen", "spectrum")
    app.go("clock")
    fx = dict(_load_fixture(), status="play")
    app.store.apply_pushstate(fx)        # play -> @nowplaying -> spectrum
    app.log("current screen:", app.fsm.current.name)
    screen = app.fsm.screens["spectrum"]
    n = screen.bars
    app.log("=== sweep (moving band) ===")
    for f in range(sweep):
        c = f % n
        target = [max(0.0, 1.0 - abs(i - c) / 4.0) for i in range(n)]
        screen.feed(target)
        app.render()
    app.log("=== decay (feed zeros; bars fall gradually) ===")
    for f in range(5):
        screen.feed([0.0] * n)
        app.render()


def run_spectrum_live(seconds=6, interval=0.1, frames_dir="var/frames"):
    app = build_sim_app(frames_dir=frames_dir)
    app.settings.set("display", "screen", "spectrum")
    app.go("spectrum")
    app.log("=== spectrum: reading live bars from the display fifo ===")
    n = max(1, int(seconds / interval))
    for i in range(n):
        time.sleep(interval)
        app.render()
    app.fsm.current.on_exit()
    app.log("=== spectrum live render complete ===")


# --- real hardware (SSD1322 over SPI) ---------------------------------------

def _quadify_active():
    """True if the live plugin is running -- a hard gate before we open SPI."""
    import subprocess
    try:
        r = subprocess.run(["systemctl", "is-active", "quadify.service"],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() == "active"
    except Exception:
        return False


_SABLE_CAVA_FIFO = "/tmp/sable-cava.fifo"
_SABLE_DISPLAY_FIFO = "/tmp/sable-display.fifo"
_MPD_PCM_FIFO = "/tmp/cava.fifo"   # MPD's real PCM output (Volumio "my_fifo")
_CAVA_BIN = "/data/plugins/system_hardware/quadify/cava/bin/cava"


def _start_live_spectrum_source(root, log=print):
    """LIVE spectrum feed: one cava instance reading MPD's REAL PCM fifo
    (/tmp/cava.fifo) via config/cava-live.conf, writing ASCII bars to Sable's own
    /tmp/sable-display.fifo. This is the production path -- it REPLACES quadify's
    cava.service, which must be stopped first (only one reader may drain the PCM
    fifo). No synthetic tone: bars track whatever Volumio is actually playing."""
    import subprocess
    conf = os.path.join(root, "config", "cava-live.conf")
    if not os.path.exists(_SABLE_DISPLAY_FIFO):
        os.mkfifo(_SABLE_DISPLAY_FIFO)
    cava = subprocess.Popen([_CAVA_BIN, "-p", conf])
    log("live spectrum source: cava on %s -> %s" % (_MPD_PCM_FIFO, _SABLE_DISPLAY_FIFO))
    return [cava]


def _start_spectrum_source(root, log=print):
    """Bench spectrum feed: a 2nd cava instance on SABLE's OWN fifos + config,
    fed by the synthetic test tone. NEVER touches the live cava/MPD pipeline."""
    import subprocess
    import shlex
    conf = os.path.join(root, "config", "cava.conf")
    tone_py = os.path.join(root, "tools", "test_tone.py")
    for f in (_SABLE_CAVA_FIFO, _SABLE_DISPLAY_FIFO):
        if not os.path.exists(f):
            os.mkfifo(f)
    # cava reads sable-cava.fifo, writes ascii bars to sable-display.fifo.
    cava = subprocess.Popen([_CAVA_BIN, "-p", conf])
    # Child shell opens the input fifo for writing (so the parent never blocks).
    tone = subprocess.Popen(
        "exec python3 %s > %s" % (shlex.quote(tone_py), shlex.quote(_SABLE_CAVA_FIFO)),
        shell=True)
    log("spectrum source: cava + test_tone on /tmp/sable-*.fifo")
    return [tone, cava]


def run_hardware(stage="clock", rotate=hardware.OLED.rotate, contrast=None,
                 fps=20, log=print):
    """FIRST real-OLED path. Hard-refuses while the live plugin runs so it can
    never contend for SPI/GPIO. Foreground; Ctrl-C releases SPI/GPIO cleanly.

    Stages (incremental first-contact):
      clock     splash -> NTP gate -> clock          (proves SSD1322 init + blit)
      modern    clock + LIVE Volumio listener (RO)   (play in Volumio -> modern)
      spectrum  force the meter screen, fed by the bench test tone
      full      clock + listener + rotary (scroll/select on the real panel)
    """
    if _quadify_active():
        print("Refusing --hardware: quadify.service is ACTIVE. Stop it first "
              "(sudo systemctl stop quadify.service) so SPI/GPIO are free.",
              file=sys.stderr)
        return 2

    import signal
    from .display.oled import OledDisplay

    if contrast is None:
        contrast = hardware.CONTRAST.medium
    root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))

    settings = Settings()
    log("opening SSD1322 (rotate=%d, contrast=%d, fps=%d) ..." % (rotate, contrast, fps))
    display = OledDisplay(hardware.OLED, rotate=rotate, log=log)
    display.set_contrast(contrast)
    # dry_run=False on real hardware: transport commands (IR/IPC next/previous/
    # toggle) must actually drive Volumio, not just log.
    app = App(display, settings, dry_run=False, log=log)

    stop = threading.Event()
    listener = None
    rotary = None
    ir = None
    ipc = None
    buttons = None
    procs = []

    def shutdown(*_a):
        stop.set()

    def tick_loop():
        period = 1.0 / max(1, fps)
        while not stop.is_set():
            try:
                app.tick_idle(time.monotonic())
                app.reconcile_screen()  # backstop missed play edges (stuck-on-clock)
                app.render()  # no-op while asleep
            except Exception as e:  # keep the panel alive across a bad frame
                log("render error:", e)
            time.sleep(period)

    try:
        signal.signal(signal.SIGINT, shutdown)
        signal.signal(signal.SIGTERM, shutdown)

        app.go("splash")
        splash = app.fsm.screens["splash"]
        threading.Thread(target=tick_loop, daemon=True, name="sable-tick").start()

        def waiting():
            splash.subtitle = "waiting for clock"

        synced = clock_gate.wait_for_clock(timeout=45.0, on_wait=waiting)
        log("clock trustworthy:", synced)
        app.go("clock")

        if stage in ("modern", "full"):
            from .volumio.listener import VolumioListener
            listener = VolumioListener(app.store, log=log)
            app.listener = listener
            # Route async browse responses to the BrowseScreen.
            listener.on_browse = app.fsm.screens["browse"].on_browse_data
            listener.start()
            log("Volumio listener started.")

        if stage == "spectrum":
            app.settings.set("display", "screen", "spectrum")
            procs = _start_spectrum_source(root, log=log)
            app.go("spectrum")  # force it: test tone, not live playback

        if stage == "full":
            # Real-music spectrum: Sable's own cava on MPD's live PCM fifo, so the
            # meter screen tracks actual playback (quadify's cava.service stopped).
            procs = _start_live_spectrum_source(root, log=log)
            from .inputs.rotary import RotaryEncoder
            rotary = RotaryEncoder(
                hardware.ROTARY,
                on_scroll=lambda d: app.handle("scroll", d),
                on_select=lambda: app.handle("select"),
                on_long_press=lambda: app.handle("home"),
            )
            rotary.start()
            log("rotary started (BCM%d/%d/%d)."
                % (hardware.ROTARY.clk, hardware.ROTARY.dt, hardware.ROTARY.sw))

            from .inputs.ir import IrListener
            ir = IrListener(app.handle, log=log)
            ir.start()
            log("IR listener started.")

            from .ipc import CommandServer
            ipc = CommandServer(app.handle, log=log)
            ipc.start()

            from .inputs.buttons import ButtonsLeds
            buttons = ButtonsLeds(hardware.MCP, app.handle, app.store, log=log)
            buttons.start()

        log("=== Sable on hardware: stage=%s. Ctrl-C to release SPI/GPIO. ===" % stage)
        while not stop.is_set():
            time.sleep(0.2)
    finally:
        log("releasing hardware ...")
        stop.set()
        if rotary is not None:
            rotary.stop()
        if buttons is not None:
            try:
                buttons.stop()
            except Exception:
                pass
        if ir is not None:
            ir.stop()
        if ipc is not None:
            try:
                ipc.stop()
            except Exception:
                pass
        if listener is not None:
            try:
                listener.stop()
            except Exception:
                pass
        for p in procs:
            try:
                p.terminate()
            except Exception:
                pass
        try:
            display.sleep()
        except Exception:
            pass
        display.cleanup()
        log("SPI/GPIO released.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="sable.app")
    ap.add_argument("--sim", action="store_true", default=True)
    ap.add_argument("--hardware", action="store_true")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--modern-fixture", action="store_true")
    ap.add_argument("--modern-live", action="store_true")
    ap.add_argument("--spectrum-fixture", action="store_true")
    ap.add_argument("--spectrum-live", action="store_true")
    ap.add_argument("--seconds", type=int, default=12)
    ap.add_argument("--interval", type=float, default=2.0)
    ap.add_argument("--frames-dir", default="var/frames")
    ap.add_argument("--stage", default="clock",
                    choices=["clock", "modern", "spectrum", "full"])
    ap.add_argument("--rotate", type=int, default=hardware.OLED.rotate,
                    choices=[0, 1, 2, 3],
                    help="luma rotate (x90 deg); default = frozen contract; "
                         "override only if the panel reads upside-down")
    ap.add_argument("--contrast", type=int, default=None,
                    help="0-255; default = hardware medium (150)")
    ap.add_argument("--fps", type=int, default=20)
    args = ap.parse_args(argv)

    if args.hardware:
        return run_hardware(stage=args.stage, rotate=args.rotate,
                            contrast=args.contrast, fps=args.fps)

    if args.modern_fixture:
        run_modern_fixture(frames_dir=args.frames_dir, interval=args.interval)
        return 0
    if args.modern_live:
        run_modern_live(seconds=args.seconds, interval=args.interval,
                        frames_dir=args.frames_dir)
        return 0
    if args.spectrum_fixture:
        run_spectrum_fixture(frames_dir=args.frames_dir)
        return 0
    if args.spectrum_live:
        run_spectrum_live(seconds=args.seconds, interval=args.interval,
                          frames_dir=args.frames_dir)
        return 0

    app = build_sim_app(frames_dir=args.frames_dir)
    if args.demo:
        app.run_demo()
        return 0

    from .inputs.sim_input import run_stdin
    from .ipc import CommandServer
    server = CommandServer(app.handle, log=print)
    server.start()
    app.go("clock")
    try:
        run_stdin(app.handle)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
