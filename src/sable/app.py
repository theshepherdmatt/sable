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
from .screens.home import HomeScreen
from .screens.shutdown import ShutdownScreen
from .screens.splash import SplashScreen
from .settings import Settings
from .state import StateStore

TRANSPORT = {"play", "pause", "toggle", "next", "previous", "random", "repeat"}
# Sources where the SOURCE (phone/app) owns transport, so a receiver-side
# play/pause/skip is futile -- it either does nothing or the source immediately
# resumes (AirPlay). Pressing transport here shows a hint instead of fighting it.
_SOURCE_CONTROLLED = frozenset({"airplay", "airplay_emulation"})
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


# Sources whose audio does NOT pass through MPD's PCM fifo (they use their own
# player), so CAVA sees nothing and a spectrum would be blank. Identified by the
# Volumio `service` value. ONE gate -- keep the set here, not scattered.
# NOTE: standard Volumio "webradio" DOES play through MPD, so it FEEDS CAVA and is
# deliberately NOT excluded (it gets a spectrum). Only own-player sources below.
_NON_CAVA_SERVICES = frozenset({
    "rp2", "radioparadise", "radio_paradise", "motherearthradio",
    "airplay", "airplay_emulation", "spop", "spotify",
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
        # Wide, cover-cropped art for the full-bleed Cinema theme.
        self.albumart_cinema = AlbumArtCache(
            size=(display.width, display.height), mode="cover", log=log)
        self.icons = IconCache(log=log)
        self.dry_run = dry_run
        self.log = log
        self._render_lock = threading.Lock()
        self.listener = None   # set on hardware/live runs; BrowseScreen uses it
        screens = [SplashScreen(self), ClockScreen(self), MenuScreen(self),
                   ModernScreen(self), MeterScreen(self), BrowseScreen(self),
                   HomeScreen(self), ShutdownScreen(self)]
        self.fsm = FSM(self, screens, log=log)
        self.store.subscribe(self._on_state)
        self.last_input = time.monotonic()
        self.asleep = False
        # Latched by _shutdown_sequence once the panel has been blanked for
        # power-off. One-way: nothing clears it, because there is no "after" --
        # see render(), which stops painting so the tick thread cannot undo the
        # blank in the seconds between our poweroff request and the power
        # actually going.
        self._shutting_down = False
        self._shutdown_started = False
        # Burn-in ladder: awake (user brightness) -> dim (reduced contrast +
        # pixel-shift) -> dark (faded off). Contrast is owned here so the ladder
        # can lower it and restore the user's brightness on wake.
        self._base_contrast = hardware.CONTRAST.medium
        self._cur_contrast = self._base_contrast
        self._idle_tier = "awake"
        # Screen-to-screen crossfade (dissolve old frame into new over _fade_s).
        self._last_frame = None
        self._rendered_screen = None
        self._fade = None              # (old_image, start_monotonic) | None
        self._fade_s = 0.22
        self._stop_timer = None        # deferred stop->clock fall-back (Fix 2)
        self._stop_grace_s = 1.5
        self._last_switch_t = 0.0      # auto-transition cooldown (Fix 3)
        self._switch_cooldown_s = 0.5
        self._first_state = True       # suppress stale-stop bounce on boot (Fix 4)
        # Paused-idle -> clock: Volumio PAUSES (not stops) most local content when
        # it goes idle, so the clock must follow a long pause too. A paused now-
        # playing screen stays up (you may resume); after clock_after_s it falls to
        # the clock. _pause_idle marks that fall so reconcile_screen leaves it.
        self._pause_timer = None
        self._pause_idle = False
        self._osd = None               # (big, small, until_monotonic) | None
        self._osd_volume = False       # the live OSD shows the current volume + bar

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
        playing (or recently paused), the clock when stopped or paused-idle. Used
        for menu/browse exits."""
        st = self.store.get().status
        if st == "play" or (st == "pause" and not self._pause_idle):
            return self.nowplaying_screen()
        return "clock"

    def reconcile_screen(self):
        """Backstop for the edge-triggered screen switch. Run every render tick while
        playing; corrects two things and never touches menu/browse:
          (a) parked on the clock while audio plays (a 'play' edge dropped because we
              were in a menu/browse/splash) -> go to now-playing; and
          (b) the resolved now-playing screen changed while still playing -- e.g. the
              source switched MPD<->rp2, so spectrum<->modern -- with no clock flash."""
        if self.asleep or self.fsm.current is None:
            return
        status = self.store.get().status
        if status not in ("play", "pause"):
            return
        if status == "pause" and self._pause_idle:
            return                      # intentionally idle on the clock; leave it
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
        # Paused-idle -> clock timer: arm on the pause edge, clear on anything else.
        if new.status == "pause" and old.status != "pause":
            self._pause_idle = False
            self._arm_pause_timer()
        elif new.status != "pause":
            self._cancel_pause_timer()
            self._pause_idle = False
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

    # --- deferred pause -> clock (idle clock for paused content) ---
    def _arm_pause_timer(self):
        self._cancel_pause_timer()
        secs = self.settings.get("screensaver", "clock_after_s", default=300)
        if not secs:
            return
        self._pause_timer = threading.Timer(secs, self._pause_timer_fire)
        self._pause_timer.daemon = True
        self._pause_timer.start()

    def _cancel_pause_timer(self):
        if self._pause_timer is not None:
            self._pause_timer.cancel()
            self._pause_timer = None

    def _pause_timer_fire(self):
        # Still paused after the grace -> fall to the clock. Reset the idle timer so
        # the clock appears at full brightness and only THEN dims (dim_s) / sleeps.
        self._pause_timer = None
        if self.store.get().status == "pause":
            self._pause_idle = True
            self.last_input = time.monotonic()
            self._stamp_switch()
            self.go("clock")

    # --- auto-transition cooldown (Fix 3) ---
    def _switch_due(self):
        """True if enough time has passed since the last AUTOMATIC screen switch.
        Throttles playback/source-driven switches (play edge, reconcile, stop) so a
        pushState burst can't flicker; user navigation (menu/back/select via go) is
        never gated. reconcile retries each tick, so it still converges after a burst."""
        return (time.monotonic() - self._last_switch_t) >= self._switch_cooldown_s

    def _stamp_switch(self):
        self._last_switch_t = time.monotonic()

    # --- brightness / contrast (owned here so the idle ladder can dim/restore) ---
    def _set_contrast(self, v):
        self._cur_contrast = max(0, min(255, int(v)))
        try:
            self.display.set_contrast(self._cur_contrast)
        except Exception:
            pass

    def set_base_contrast(self, v):
        """Set the user's brightness. Applied immediately unless the panel is
        currently dimmed/dark by the idle ladder (it restores to this on wake)."""
        self._base_contrast = max(1, min(255, int(v)))
        if self._idle_tier == "awake" and not self.asleep:
            self._set_contrast(self._base_contrast)

    def set_brightness_from_settings(self):
        level = self.settings.get("display", "brightness", default="medium")
        self.set_base_contrast({"low": hardware.CONTRAST.low,
                                "medium": hardware.CONTRAST.medium,
                                "high": hardware.CONTRAST.high}.get(
                                    level, hardware.CONTRAST.medium))

    def _dim_contrast(self):
        return max(hardware.CONTRAST.low // 2, self._base_contrast // 3)

    # --- idle journey: awake -> dim -> dark (burn-in protection) ---
    def note_activity(self):
        """Any real input (rotary/IR/IPC) or resumed playback: reset the idle
        timer and, if the panel had dimmed or slept, return it to full user
        brightness at once (a deliberate user action wants an instant response;
        only the fade-to-DARK is gradual)."""
        self.last_input = time.monotonic()
        if self.asleep or self._idle_tier != "awake":
            was_asleep = self.asleep
            self.asleep = False
            self._idle_tier = "awake"
            if was_asleep:
                try:
                    self.display.wake()
                except Exception:
                    pass
            self._set_contrast(self._base_contrast)
            self.log("screensaver: wake")
            self.render()

    def tick_idle(self, now):
        """Called each render tick. Playing or an open menu counts as activity
        (stay awake); otherwise walk the ladder by idle time: dim_s -> reduced
        contrast + pixel-shift, idle_s -> fade to dark then OLED off. Either
        threshold == 0 disables that tier.

        A PAUSED now-playing screen is kept bright (you may resume) until the
        pause timer falls it to the clock; only then does the dim/sleep ladder
        run -- so the clock appears bright before it dims."""
        cur = self.fsm.current.name if self.fsm.current is not None else None
        if self.store.get().status == "play" or cur == "menu":
            self.note_activity()
            return
        if cur in ("modern", "spectrum") and not self._pause_idle:
            self._tier_awake()
            return
        idle = now - self.last_input
        idle_s = self.settings.get("screensaver", "idle_s", default=3600)
        dim_s = self.settings.get("screensaver", "dim_s", default=120)
        if idle_s and idle >= idle_s:
            self._tier_dark()
        elif dim_s and idle >= dim_s:
            self._tier_dim()
        else:
            self._tier_awake()

    def _tier_awake(self):
        if self._idle_tier != "awake":
            self._idle_tier = "awake"
            self._set_contrast(self._base_contrast)

    def _tier_dim(self):
        if self.asleep:
            return
        if self._idle_tier != "dim":
            self._idle_tier = "dim"
            self._set_contrast(self._dim_contrast())
            self.log("screensaver: dim")

    def _tier_dark(self):
        """Fade the contrast to 0 over a few render ticks (graceful, not a snap),
        then power the panel off (0xAE). Idempotent once asleep."""
        if self.asleep:
            return
        self._idle_tier = "dark"
        step = max(1, self._base_contrast // 12)
        self._set_contrast(self._cur_contrast - step)
        if self._cur_contrast <= 0:
            self.asleep = True
            # Drop any in-flight fade + stale frame so WAKE is instant, not a
            # dissolve from the pre-sleep image.
            self._fade = None
            self._last_frame = None
            self._rendered_screen = None
            try:
                self.display.sleep()
            except Exception:
                pass
            self.log("screensaver: OLED off (idle)")

    # --- rendering ---
    def render(self):
        if self.asleep or self._shutting_down:
            return
        from PIL import ImageDraw
        with self._render_lock:
            img = self.display.blank_canvas()
            draw = ImageDraw.Draw(img)
            cur = self.fsm.current
            if cur is not None:
                cur.render(img, draw, self.display.width, self.display.height)
            if self._idle_tier == "dim":
                img = self._burn_in_shift(img)
            cur_name = cur.name if cur is not None else None
            if cur_name != self._rendered_screen:
                # Self-heal any diffing desync right at the transition boundary
                # -- see OledDisplay.force_full_redraw() for why.
                try:
                    self.display.force_full_redraw()
                except Exception:
                    pass
            out = self._with_transition(img, cur_name)
            out = self._draw_osd(out)
            self.display.present(out)
            self._last_frame = img
            self._rendered_screen = cur_name

    # --- transient OSD overlay (volume / mute / DAC-input hints) -------------
    def show_osd(self, big, small="", duration=1.4):
        """Flash a centred overlay for `duration`s over whatever is on screen.
        Used for mute / DAC-input hints (some are open-loop: a DAC that owns volume
        handles the change and Sable just shows feedback)."""
        self._osd = (big, small, time.monotonic() + duration)
        self._osd_volume = False
        self.render()

    def nudge_volume(self, delta):
        """Rotary/IR volume: drive Volumio's mixer (a no-op on a DAC that owns
        volume) and flash a LIVE volume OSD (number + bar, re-read each frame so it
        tracks Volumio's volume event)."""
        self.note_activity()
        delta = int(delta or 0)
        if self.listener is not None and delta:
            # Honour the turn magnitude: a fast spin (rotary acceleration, |delta|
            # up to 4) moves the volume that many steps instead of a single tick,
            # so you are not cranking the knob forever. Capped so a runaway value
            # can't spam the mixer.
            sign = "+" if delta > 0 else "-"
            for _ in range(min(abs(delta), 10)):
                self.listener.set_volume(sign)
        self._osd = (None, "VOLUME", time.monotonic() + 1.3)
        self._osd_volume = True
        self.render()

    def _draw_osd(self, img):
        if self._osd is None:
            return img
        big, small, until = self._osd
        if time.monotonic() >= until:
            self._osd = None
            self._osd_volume = False
            return img
        from PIL import Image, ImageDraw
        from .screens.base import crisp_text
        out = Image.blend(img, Image.new("L", img.size, 0), 0.8)   # dim the screen
        w, h = img.size
        if self._osd_volume:
            vol = max(0, min(100, self.store.get().volume))
            crisp_text(out, (w // 2, 11), "VOLUME",
                       self.fonts.get("sans", 9), fill=150, anchor="mm")
            crisp_text(out, (w // 2, 30), "%d" % vol,
                       self.fonts.get("sans_bold", 22), fill=255, anchor="mm")
            d = ImageDraw.Draw(out)
            bw = int(w * 0.62)
            bx = (w - bw) // 2
            by = h - 13
            d.rectangle((bx, by, bx + bw, by + 5), outline=80)
            fillw = int(bw * vol / 100)
            if fillw > 0:
                d.rectangle((bx, by, bx + fillw, by + 5), fill=225)
            return out
        if small:
            crisp_text(out, (w // 2, h // 2 - 14), small,
                       self.fonts.get("sans", 11), fill=160, anchor="mm")
        crisp_text(out, (w // 2, h // 2 + 7), big,
                   self.fonts.get("sans_bold", 26), fill=255, anchor="mm")
        return out

    def _with_transition(self, img, cur_name):
        """Crossfade: when the screen changed since the last present, dissolve the
        previous frame into the new one over _fade_s. A pure function of time, so
        the render tick animates it; never a barrier. Disable via display.transitions."""
        if not self.settings.get("display", "transitions", default=True):
            return img
        if (self._last_frame is not None and cur_name != self._rendered_screen
                and self._fade is None):
            self._fade = (self._last_frame, time.monotonic())
        if self._fade is None:
            return img
        old, t0 = self._fade
        alpha = (time.monotonic() - t0) / self._fade_s
        if alpha >= 1.0 or old.size != img.size or old.mode != img.mode:
            self._fade = None
            return img
        from PIL import Image
        return Image.blend(old, img, alpha)

    def _burn_in_shift(self, img):
        """While idle-dimmed, drift the whole frame by a few px on a slow cycle so
        a static clock never burns a fixed image into the OLED. Pixels shifted in
        from the edge are black (panel off there) -- invisible on an OLED."""
        import math
        t = time.monotonic()
        dx = int(round(3 * math.sin(t / 11.0)))
        dy = int(round(2 * math.sin(t / 17.0)))
        if dx == 0 and dy == 0:
            return img
        out = self.display.blank_canvas()
        out.paste(img, (dx, dy))
        return out

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
            # Long-press = universal escape: jump out to the sources carousel from
            # anywhere, and from the carousel itself out to now-playing/clock. So
            # there is ALWAYS a way back, even from an empty browse list.
            if self.fsm.current is not None and self.fsm.current.name == "home":
                self.go(self.base_screen())
            else:
                self.go("home")
            self.render()
        elif cmd == "play_uri":
            if self.listener is not None and arg:
                self.listener.play_uri(str(arg))
                self.show_osd("RADIO", str(arg))
        elif cmd == "play_playlist":
            if self.listener is not None and arg:
                self.listener.play_playlist(str(arg))
                self.show_osd("PLAYLIST", str(arg))
        elif cmd in TRANSPORT:
            if self._source_controls_transport():
                self.show_osd("AIRPLAY", "source controls")
            else:
                self._transport(cmd)
        elif cmd == "volume":
            self.nudge_volume(1 if arg == "+" else -1)
        elif cmd == "mute":
            if self.listener is not None:
                self.listener.set_volume("mute")
            self.show_osd("MUTE")
        elif cmd == "dac_input":
            self._dac_input()
        elif cmd == "reload_config":
            self.settings.load()
            # Re-apply settings that are set imperatively (not read each frame):
            # brightness -> contrast. screen/style/transitions/clock/screensaver
            # are read live in render()/tick, so they apply on the next frame.
            self.set_brightness_from_settings()
            self.log("config reloaded")
            self.render()
        elif cmd == "shutdown":
            self.begin_shutdown()
        else:
            self.log("unknown cmd:", cmd)

    # --- power off ------------------------------------------------------------
    def begin_shutdown(self):
        """Button 8 (hold): message -> blank panel -> power off.

        Runs on its own thread. handle() is called from the BUTTON scan thread,
        and this sequence deliberately sleeps (the message has to be readable,
        and the blank has to land before the power does); blocking there would
        freeze every button, the LEDs and the matrix scan for the duration.
        Idempotent -- a second hold while this is already running is ignored
        rather than starting a second poweroff."""
        if self._shutdown_started:
            self.log("shutdown: already in progress")
            return
        self._shutdown_started = True
        threading.Thread(target=self._shutdown_sequence, daemon=True,
                         name="sable-shutdown").start()

    def _shutdown_sequence(self):
        hold_s = self.settings.get("display", "shutdown_message_s", default=1.6)
        try:
            hold_s = max(0.0, float(hold_s))
        except (TypeError, ValueError):
            hold_s = 1.6

        # 1. CLEAR, then show the message. Both matter:
        #    - the crossfade would dissolve the now-playing screen into this one,
        #      so for ~_fade_s the panel shows a ghost of what we're leaving.
        #      Dropping _last_frame gives _with_transition nothing to fade from.
        #    - clear() wipes GRAM outright, so no part of the old screen can
        #      survive underneath in a region the diff considers unchanged.
        with self._render_lock:
            self._fade = None
            self._last_frame = None
            self._rendered_screen = None
            try:
                self.display.clear()
            except Exception as e:
                self.log("shutdown: clear failed (continuing):", e)
        # Full brightness: the idle ladder may have dimmed the panel, and the
        # last thing the user sees should not be a faint message.
        self._set_contrast(self._base_contrast)
        self.go("shutdown")

        # 2. Hold it long enough to actually read.
        time.sleep(hold_s)

        # 3. Blank. Latch FIRST: the tick thread renders continuously, so a
        #    blank applied before render() is stopped is simply repainted on the
        #    next tick and the panel stays lit into the power cut.
        self._shutting_down = True
        with self._render_lock:
            try:
                self.display.clear()
                self.display.sleep()
            except Exception as e:
                self.log("shutdown: blank failed (continuing):", e)

        # 4. Only now ask the OS to go down.
        if self.dry_run:
            self.log("[dry-run] would poweroff")
            return
        self.log("poweroff (button 8 hold)")
        self._poweroff()

    def _poweroff(self):
        # Volumio: there is no valid `volumio shutdown` CLI subcommand (checked
        # via --help; a bare Popen just printed usage and exited 0, doing
        # nothing). Volumio's own web UI power button emits a bare 'shutdown'
        # event over Socket.IO (websocket/index.js: connWebSocket.on('shutdown',
        # ...) -> commandRouter.shutdown()), and Sable already holds that same
        # live connection -- so reuse it rather than guess at a REST route.
        sio = getattr(self.listener, "sio", None) if self.listener else None
        if sio is not None:
            try:
                sio.emit("shutdown")
                return
            except Exception as e:
                self.log("shutdown: socket.io emit failed:", e)
        # moOde (and Volumio with a dead socket) has no such event. Fall back to
        # systemd directly. Without this the moOde path blanked the panel and
        # then just sat there: the old code logged "no live Volumio connection"
        # and returned, so the Pi never powered off and the only way out was
        # pulling the plug -- the exact thing a shutdown button exists to avoid.
        # Needs the sudoers rule installed by install.sh / install-moode.sh.
        import subprocess
        for argv in (["sudo", "-n", "/bin/systemctl", "poweroff"],
                     ["sudo", "-n", "/sbin/poweroff"]):
            try:
                if subprocess.call(argv) == 0:
                    return
            except Exception as e:
                self.log("shutdown: %s failed: %s" % (argv[2], e))
        self.log("shutdown error: no way to power off (no Volumio socket, "
                 "and passwordless sudo poweroff was refused)")

    def _source_controls_transport(self):
        return (self.store.get().service or "").strip().lower() in _SOURCE_CONTROLLED

    def _transport(self, cmd):
        # Fire-and-forget: the `volumio` CLI can block for many seconds (seen at
        # ~17s during AirPlay), and this runs on the INPUT thread (button/IR/
        # rotary) -- a synchronous call would freeze all input. Popen returns at
        # once; we don't need the result.
        if self.dry_run:
            self.log("[dry-run] volumio", cmd)
            return
        import subprocess
        try:
            subprocess.Popen(["volumio", cmd],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            self.log("transport error:", cmd, e)

    def _dac_input(self):
        idx = (self.settings.get("dac", "input_index", default=0) + 1) % len(hardware.DAC_INPUTS)
        self.settings.set("dac", "input_index", idx)
        label = hardware.DAC_INPUTS[idx]
        self.log("DAC input ->", label, "(hint; user-correctable)")
        self.show_osd(label, "INPUT")

    def respawn_cava(self, reason="stuck"):
        """Kill and relaunch the live cava process. Wired as FifoBars' on_stuck
        callback (see display/fifo_meter.py) -- cava can desync internally (still
        running, still writing well-formed frames, but every value stuck at 0)
        rather than crashing outright, so nothing else notices it needs a kick.
        _cava_procs/_cava_launch are set by _start_live_spectrum_source; absent
        on moOde or the bench/spectrum stages, where there's nothing to respawn."""
        procs = getattr(self, "_cava_procs", None)
        launch = getattr(self, "_cava_launch", None)
        if procs is None or launch is None:
            self.log("cava: respawn requested (%s) but not running under the "
                     "live-spectrum setup -- ignoring" % reason)
            return
        # Only act when audio should actually be flowing. All-zero bars are the
        # CORRECT reading for a stopped or paused player, so respawning then
        # fixes nothing and would churn a process every cooldown while the box
        # sits idle. Under `play` the same reading means cava is genuinely
        # broken -- almost always because it opened /tmp/cava.fifo before MPD
        # had a writer on it, which is exactly the cold-boot case. cava opens
        # its input once and never retries, so a respawn is the only cure.
        if self.store.get().status != "play":
            self.log("cava: stuck (%s) but nothing is playing -- deferring "
                     "respawn until playback starts" % reason)
            return
        self.log("cava: respawning (%s)" % reason)
        for p in list(procs):
            try:
                p.terminate()
            except Exception:
                pass
            procs.remove(p)
        cava_bin, conf = launch
        import subprocess
        try:
            cava = subprocess.Popen([cava_bin, "-p", conf])
            procs.append(cava)
            self.log("cava: respawned")
        except Exception as e:
            self.log("cava: respawn failed:", e)

    # --- Phase 0 scripted slice ---
    def run_demo(self):
        self.log("=== Sable Phase 0 vertical slice ===")
        self.go("splash")

        synced = clock_gate.wait_for_clock(timeout=45.0)
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


def _platform():
    """Which player backend to talk to: 'volumio' (default) or 'moode', set by
    install.sh / install-moode.sh via the environment. Everything downstream of
    the listener (state.py, screens, FSM) is backend-agnostic."""
    return os.environ.get("SABLE_PLATFORM", "volumio").strip().lower()


def _make_listener(store, log=print):
    if _platform() == "moode":
        from .moode.listener import MoodeListener
        return MoodeListener(store, log=log)
    from .volumio.listener import VolumioListener
    return VolumioListener(store, log=log)


def run_modern_live(seconds=12, interval=2.0, frames_dir="var/frames"):
    app = build_sim_app(frames_dir=frames_dir)
    app.go("clock")
    listener = _make_listener(app.store, log=app.log)
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
    """True if the predecessor app is running -- a hard gate before we open SPI.
    quadify.service on Volumio, quoode.service on moOde (same predecessor code,
    different name per platform)."""
    import subprocess
    unit = "quoode.service" if _platform() == "moode" else "quadify.service"
    try:
        r = subprocess.run(["systemctl", "is-active", unit],
                           capture_output=True, text=True, timeout=5)
        return r.stdout.strip() == "active"
    except Exception:
        return False


_SABLE_CAVA_FIFO = "/tmp/sable-cava.fifo"
_SABLE_DISPLAY_FIFO = "/tmp/sable-display.fifo"
_MPD_PCM_FIFO = "/tmp/cava.fifo"   # MPD's real PCM output (Volumio "my_fifo")
# Fall-back to the quadify plugin's bundled cava ONLY if the system one is absent;
# a fresh install gets cava from apt (`apt install cava`), so no quadify needed.
_QUADIFY_CAVA = "/data/plugins/system_hardware/quadify/cava/bin/cava"


def _resolve_cava(log=print):
    """Prefer a system-installed cava (apt); else the quadify plugin's bundled
    binary; else None (spectrum stays flat -- logged, not fatal)."""
    import shutil
    path = shutil.which("cava")
    if not path and os.path.exists(_QUADIFY_CAVA):
        path = _QUADIFY_CAVA
    if not path:
        log("cava: no binary found ('apt install cava') -- spectrum disabled")
    return path


def _wait_for_mpd_fifo(timeout=90.0, poll=1.0, log=print):
    """Block until Volumio has actually created _MPD_PCM_FIFO.

    mpd.service being "active" is not enough: Volumio's backend applies its own
    MPD output config (which creates the sable_fifo output's pipe on disk) well
    after the mpd process starts -- observed ~2 minutes on a cold boot. cava opens
    its input path ONCE at startup and never retries, so spawning it before this
    file exists leaves it permanently silent until something respawns it. Returns
    True if the fifo appeared, False if it gave up (caller still starts cava --
    better a one-shot retry-less attempt than none)."""
    deadline = time.monotonic() + timeout
    while not os.path.exists(_MPD_PCM_FIFO):
        if time.monotonic() >= deadline:
            log("cava: %s never appeared after %.0fs -- starting cava anyway"
                % (_MPD_PCM_FIFO, timeout))
            return False
        time.sleep(poll)
    return True


def _start_live_spectrum_source(root, app, log=print):
    """LIVE spectrum feed: one cava instance reading MPD's REAL PCM fifo
    (/tmp/cava.fifo) via config/cava-live.conf, writing ASCII bars to Sable's own
    /tmp/sable-display.fifo. This is the production path -- it REPLACES quadify's
    cava.service, which must be stopped first (only one reader may drain the PCM
    fifo). No synthetic tone: bars track whatever Volumio is actually playing.

    The fifo-wait runs in a background thread so a slow Volumio boot (fifo can
    take ~2min to appear -- see _wait_for_mpd_fifo) does not delay rotary/IR/
    buttons startup, which happen right after this returns. procs is returned
    immediately (possibly still empty) and mutated in place once cava actually
    launches; the caller cleanup loop (which terminates each proc) sees
    the same list object, so this is safe without extra synchronisation.

    Also stashes (procs, cava_bin, conf) on app so App.respawn_cava() can
    kill+relaunch cava later without duplicating the launch details -- see
    fifo_meter.FifoBars' on_stuck, wired from the screens that read cava's
    output, which detects cava silently desyncing mid-session (still running,
    still writing frames, but every value stuck at 0)."""
    import subprocess
    import threading
    conf = os.path.join(root, "config", "cava-live.conf")
    if not os.path.exists(_SABLE_DISPLAY_FIFO):
        os.mkfifo(_SABLE_DISPLAY_FIFO)
    cava_bin = _resolve_cava(log)
    if not cava_bin:
        return []
    procs = []
    app._cava_procs = procs
    app._cava_launch = (cava_bin, conf)

    def _launch():
        _wait_for_mpd_fifo(log=log)
        cava = subprocess.Popen([cava_bin, "-p", conf])
        procs.append(cava)
        log("live spectrum source: cava on %s -> %s" % (_MPD_PCM_FIFO, _SABLE_DISPLAY_FIFO))

    threading.Thread(target=_launch, daemon=True, name="sable-cava-wait").start()
    return procs


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
    cava_bin = _resolve_cava(log)
    if not cava_bin:
        return []
    # cava reads sable-cava.fifo, writes ascii bars to sable-display.fifo.
    cava = subprocess.Popen([cava_bin, "-p", conf])
    # Child shell opens the input fifo for writing (so the parent never blocks).
    tone = subprocess.Popen(
        "exec python3 %s > %s" % (shlex.quote(tone_py), shlex.quote(_SABLE_CAVA_FIFO)),
        shell=True)
    log("spectrum source: cava + test_tone on /tmp/sable-*.fifo")
    return [tone, cava]


def run_hardware(stage="clock", rotate=None, contrast=None,
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
        unit = "quoode.service" if _platform() == "moode" else "quadify.service"
        print("Refusing --hardware: %s is ACTIVE. Stop it first "
              "(sudo systemctl stop %s) so SPI/GPIO are free." % (unit, unit),
              file=sys.stderr)
        return 2

    import signal
    from .display.oled import OledDisplay

    root = os.path.normpath(os.path.join(os.path.dirname(__file__), "..", ".."))

    settings = Settings()
    # Rotation is a USER setting (DEGREES); the --rotate flag overrides it for
    # bench tuning. OledDisplay converts deg->luma quarter-turns.
    rotate_deg = rotate if rotate is not None else int(
        settings.get("display", "rotate", default=0) or 0)
    log("opening SSD1322 (rotate=%ddeg, fps=%d) ..." % (rotate_deg, fps))
    display = OledDisplay(hardware.OLED, rotate=rotate_deg, log=log)
    # dry_run=False on real hardware: transport commands (IR/IPC next/previous/
    # toggle) must actually drive Volumio, not just log.
    app = App(display, settings, dry_run=False, log=log)
    # Brightness from the user's saved setting (the idle ladder dims/restores from
    # this base); a --contrast flag still overrides for bench tuning.
    if contrast is None:
        app.set_brightness_from_settings()
    else:
        app.set_base_contrast(contrast)
    log("brightness: base contrast %d" % app._base_contrast)

    stop = threading.Event()
    listener = None
    rotary = None
    ir = None
    ipc = None
    buttons = None
    procs = []
    tick = None

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
        tick = threading.Thread(target=tick_loop, daemon=True, name="sable-tick")
        tick.start()

        # NB no deferred panel re-init here any more. It was added to chase the
        # cold-boot blank on the strength of a test that turned out to have been
        # run against an ALREADY-WORKING panel, so it proved nothing. The actual
        # cause was sable-boot-splash.service: a second process initialising the
        # SSD1322 before this one and leaving it in a state this process's init
        # did not recover. With that service disabled the panel comes up every
        # time, so the re-init was solving nothing and cost a visible repaint a
        # few seconds into every start. OledDisplay.reinit() is kept -- it is a
        # sound thing to have -- but nothing calls it on the happy path.

        # The listener is started BEFORE the boot gate now, not after it. The
        # gate below waits on Volumio being reachable, so the connection attempt
        # has to already be in flight -- and starting it early also means the
        # first pushState lands sooner.
        if stage in ("modern", "full"):
            listener = _make_listener(app.store, log=log)
            app.listener = listener
            # Route async browse responses to the BrowseScreen, and the live source
            # list to the home carousel.
            listener.on_browse = app.fsm.screens["browse"].on_browse_data
            listener.on_sources = app.fsm.screens["home"].refresh_sources
            listener.start()
            log("Volumio listener started.")

        # --- boot gate: hold the splash until the unit is genuinely READY ------
        # Ready means BOTH: the clock is right, and Volumio is up. Either one
        # alone leaves the panel telling a lie -- the clock screen with a bogus
        # time (ntpd has not stepped yet), or a plausible-looking clock on a box
        # that cannot actually play anything for another half minute, which
        # reads as "broken" rather than "still starting".
        #
        # The old 45s clock budget was also too tight: ntpd steps at ~40s on
        # this box, so a slightly slow network fell through to a wrong clock,
        # which is the one thing this gate exists to prevent.
        gate_s = settings.get("display", "boot_gate_s", default=120.0)
        try:
            gate_s = max(0.0, float(gate_s))
        except (TypeError, ValueError):
            gate_s = 120.0

        def _volumio_ready():
            if listener is None:
                return True          # stages with no listener: nothing to wait for
            return bool(getattr(getattr(listener, "sio", None), "connected", False))

        def _splash(msg):
            app.fsm.screens["splash"].subtitle = msg
            app.render()

        deadline = time.monotonic() + gate_s
        synced = False
        while not stop.is_set():
            if not synced:
                synced = clock_gate.is_synced()
            ready = _volumio_ready()
            if synced and ready:
                break
            if time.monotonic() >= deadline:
                log("boot gate: timed out (clock=%s volumio=%s) -- continuing"
                    % (synced, ready))
                break
            # Name what we are actually waiting on, so a slow boot looks like
            # progress rather than a hang. The player's name is the platform's,
            # not always "volumio" -- on moOde this same wait is for mpd.
            waiting_for = "moode" if _platform() == "moode" else "volumio"
            _splash("setting the clock" if not synced
                    else "waiting for %s" % waiting_for)
            time.sleep(0.5)
        log("boot gate: clock=%s volumio=%s" % (synced, _volumio_ready()))
        app.go("clock")

        if stage == "spectrum":
            app.settings.set("display", "screen", "spectrum")
            procs = _start_spectrum_source(root, log=log)
            app.go("spectrum")  # force it: test tone, not live playback

        if stage == "full":
            # Real-music spectrum. Volumio/quadify: Sable spawns its own cava on
            # MPD's live PCM fifo (quadify's cava.service stopped first). moOde:
            # nothing to spawn -- peppyalsa already taps every audio_output's PCM
            # unconditionally (see fifo_meter.PeppySpectrumBars), so the meter
            # screen's reader (make_spectrum_reader) just opens its pipe directly.
            if _platform() == "moode":
                procs = []
                log("live spectrum source: moOde peppyalsa (/tmp/peppyspectrum)")
            else:
                procs = _start_live_spectrum_source(root, app, log=log)
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

            if app.settings.get("ir", "enabled", default=True):
                from .inputs.ir import IrListener
                ir = IrListener(app.handle, app=app, log=log)
                ir.start()
                log("IR listener started.")
            else:
                log("IR listener disabled (controls.ir).")

            from .ipc import CommandServer
            ipc = CommandServer(app.handle, log=log)
            ipc.start()

            if app.settings.get("controls", "leds_enabled", default=True):
                from .inputs.buttons import ButtonsLeds
                buttons = ButtonsLeds(hardware.MCP, app.handle, app.store, app=app, log=log)
                # Boot LED sweep waits for Volumio (see ButtonsLeds.signal_ready);
                # listener was created/started earlier in this function, so guard
                # the race where it already connected before this wiring runs.
                if listener is not None:
                    listener.on_connect = buttons.signal_ready
                    if getattr(getattr(listener, "sio", None), "connected", False):
                        buttons.signal_ready()
                buttons.start()
            else:
                log("Buttons/LEDs disabled (controls.leds_enabled).")

        log("=== Sable on hardware: stage=%s. Ctrl-C to release SPI/GPIO. ===" % stage)
        while not stop.is_set():
            time.sleep(0.2)
    finally:
        log("releasing hardware ...")
        stop.set()
        # Wait for the render tick to actually stop before touching the panel.
        # stop.set() only ASKS it to; without the join, sleep()/cleanup() below
        # (and cleanup's GPIO.cleanup(), which yanks the pins luma is mid-write
        # on) race an in-flight blit. On Restart=always that leaves the
        # controller half-fed through the restart, so the next process inits over
        # a confused panel -- blank, or streaked with the old frame. One tick
        # period is the expected wait; the timeout keeps a wedged render from
        # eating our TimeoutStopSec.
        if tick is not None:
            try:
                tick.join(timeout=2.0)
            except Exception:
                pass
        # Blank the panel FIRST, before anything network-dependent (listener/IR/
        # IPC/cava terminate below). During a real system shutdown the network
        # can be torn down concurrently with this process's own stop, and any of
        # those calls hanging past sable.service's TimeoutStopSec gets this
        # process SIGKILLed -- which skips whatever cleanup hadn't run yet. Panel
        # cleanup is the one part a user actually SEES, so it must not be the
        # part that silently gets skipped when something else hangs.
        try:
            display.sleep()
        except Exception:
            pass
        try:
            display.cleanup()
        except Exception:
            pass
        log("SPI/GPIO released.")
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
    ap.add_argument("--rotate", type=int, default=None,
                    choices=[0, 90, 180, 270],
                    help="panel rotation in DEGREES; default = the display.rotate "
                         "user setting (use 180 if the panel is mounted upside-down)")
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
