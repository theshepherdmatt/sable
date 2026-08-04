"""Front-panel buttons + LEDs via the MCP23017 (I2C). Sable's OWN controller --
replaces quadify's buttonsleds daemon. Two wins over the old version: button
presses go through the SAME app.handle as the rotary/IR (one command vocabulary),
and the play/pause LED follows Sable's live StateStore (instant) instead of
shelling out to `volumio status` every 2 seconds.

Hardware contract (hardware.MCP): GPIOA = 8 LEDs (bits 0-7, one lit at a time);
GPIOB = a 2-col x 4-row button matrix (B0/B1 drive the columns, B2-B7 read the
rows with pull-ups). Button 8 + LED 8 are software-controlled like the rest on
this unit (the older Evo build reserved them for a hardware power button/LED --
not the case here).

All MCP bus access is serialized on one lock (scan reads GPIOB; LED writes go to
GPIOA), since the scan loop, the state callback, and the feedback timer can all
touch the bus concurrently.
"""
import dataclasses
import threading
import time

from ..hardware import led_byte

# LED bit positions on GPIOA (mirror hardware.py; one lit at a time).
LED_PLAY = 1 << 0
LED_PAUSE = 1 << 1
LED_PREV = 1 << 2
LED_NEXT = 1 << 3
LED_SHUFF = 1 << 4
LED_REPEAT = 1 << 5
LED_SPARE = 1 << 6
LED_SPARE2 = 1 << 7

# Boot "power on" sweep order across all eight LEDs.
LED_SWEEP = [LED_PLAY, LED_PAUSE, LED_PREV, LED_NEXT, LED_SHUFF, LED_REPEAT, LED_SPARE, LED_SPARE2]

# AirPlay: Volumio reports status=play even when paused and gives no reliable
# play/pause signal, so a status LED would lie -- show none for these services.
_AIRPLAY_SERVICES = ("airplay", "airplay_emulation")

# Panel button id -> (Sable command or None, feedback LED).
_BUTTON_ACTION = {
    1: ("play", LED_PLAY),
    2: ("pause", LED_PAUSE),
    3: ("previous", LED_PREV),
    4: ("next", LED_NEXT),
    5: ("random", LED_SHUFF),
    6: ("repeat", LED_REPEAT),
    7: (None, LED_SPARE),
    8: ("shutdown", LED_SPARE2),
}
# Matrix layout: row -> [col0, col1] button ids (matches the proven wiring).
_BUTTON_MAP = [[1, 2], [3, 4], [5, 6], [7, 8]]

# Buttons whose action only fires after a hold (not a tap) -- a bare press-edge
# is too easy to trigger by accident for something as hard to reverse as a full
# poweroff. HOLD_S is how long GPIOB must read pressed before it fires.
_HOLD_BUTTONS = {8}
_HOLD_S = 2.0


class ButtonsLeds:
    def __init__(self, mcp, handle, store, app=None, feedback_s=0.5,
                 debounce_s=0.1, log=print):
        self.mcp = mcp            # hardware.MCP dataclass
        self.handle = handle      # app.handle(cmd, arg)
        self.store = store        # StateStore -> play/pause LED
        self._app = app           # App -> idle/asleep state for the LED policy
        self.feedback_s = feedback_s
        self.debounce_s = debounce_s
        self.log = log
        self._bus = None
        self._running = False
        self._lock = threading.Lock()   # serializes ALL MCP bus access
        self._ephemeral_led = 0          # transient button-press feedback
        self._hw_led = -1                # last byte written to GPIOA
        self._timer = None
        self._prev = [[1, 1], [1, 1], [1, 1], [1, 1]]
        # Boot sweep gate: held until signal_ready() (wired to the Volumio/moOde
        # listener's on_connect) or ready_timeout_s elapses, whichever first --
        # the sweep becomes a real "system ready" indicator instead of firing
        # the instant the MCP bus is up, well before Volumio has even connected.
        # The timeout is a fallback so hardware bench-testing without a live
        # Volumio backend still gets the sweep eventually.
        self._ready = threading.Event()
        self.ready_timeout_s = 20.0
        # Hold-to-fire state for _HOLD_BUTTONS (see that constant) -- press time
        # per currently-held hold-button, and whether it has already fired (so a
        # long hold does not repeat-fire every scan tick past the threshold).
        self._hold_start = {}
        self._hold_fired = set()

    # --- lifecycle ---
    def signal_ready(self):
        """Release the boot sweep. Call from the listener's on_connect."""
        self._ready.set()

    def start(self):
        import smbus2
        try:
            self._bus = smbus2.SMBus(self.mcp.bus)
        except Exception as e:
            self.log("buttons: I2C bus unavailable, controls disabled:", e)
            return
        self._detect_addr()
        try:
            self._init_mcp()
        except Exception as e:
            self.log("buttons: MCP init failed, controls disabled:", e)
            self._bus = None
            return
        self._running = True
        threading.Thread(target=self._scan_loop, daemon=True, name="sable-buttons").start()
        threading.Thread(target=self._led_loop, daemon=True, name="sable-leds").start()
        self.log("buttons+LEDs started (MCP 0x%02x on i2c-%d)." % (self.mcp.addr, self.mcp.bus))

    def stop(self):
        self._running = False
        if self._timer:
            self._timer.cancel()
            self._timer = None
        if self._bus:
            try:
                with self._lock:
                    self._bus.write_byte_data(self.mcp.addr, self.mcp.GPIOA, 0x00)
                    self._bus.write_byte_data(self.mcp.addr, self.mcp.GPIOB, 0x03)
                    self._bus.close()
            except Exception:
                pass
            self._bus = None

    def _addr_is_overridden(self):
        """True if config/hardware.json pins mcp.addr explicitly. addr IS a real
        override key (hardware._build applies any Mcp23017 field) -- it's just
        absent from the shipped hardware.json. Explicit beats auto-detect."""
        try:
            from ..hardware import _load_overrides
            return "addr" in (_load_overrides().get("mcp") or {})
        except Exception:
            return False

    def _detect_addr(self):
        """Probe 0x20-0x27 and adopt a lone responder as the working address.

        An MCP23017 can't self-identify, so a single responder is the only safe
        pick on this hardware; zero or several -> fall back to the configured
        addr. Picks a working addr at runtime via a fresh dataclass instance --
        the frozen hardware.MCP default is never mutated. A hardware.json addr
        override always wins (explicit beats inferred)."""
        if self._addr_is_overridden():
            self.log("buttons: using configured MCP addr 0x%02x (hardware.json "
                     "override)." % self.mcp.addr)
            return
        found = []
        for addr in range(0x20, 0x28):
            try:
                self._bus.read_byte(addr)   # cheap read; ACK => device present
                found.append(addr)
            except Exception:
                pass                         # no device at this addr -- ignore
        if len(found) == 1:
            self.mcp = dataclasses.replace(self.mcp, addr=found[0])
            self.log("MCP23017 detected at 0x%02x" % found[0])
        elif not found:
            self.log("buttons: no MCP23017 found on i2c-%d (probed 0x20-0x27) -- "
                     "controls disabled." % self.mcp.bus)
        else:
            self.log("buttons: multiple I2C devices responded (%s); an MCP can't "
                     "self-identify, so not guessing -- using configured 0x%02x." %
                     (", ".join("0x%02x" % a for a in found), self.mcp.addr))

    def _init_mcp(self):
        b, a = self._bus, self.mcp.addr
        b.write_byte_data(a, self.mcp.IODIRA, 0x00)    # GPIOA all outputs (LEDs)
        b.write_byte_data(a, self.mcp.IODIRB, 0xFC)    # B0/B1 outputs (cols), B2-B7 inputs
        b.write_byte_data(a, self.mcp.GPPUB, 0xFC)     # pull-ups on the row inputs
        b.write_byte_data(a, self.mcp.GPIOA, 0x00)     # LEDs off
        b.write_byte_data(a, self.mcp.GPIOB, 0x03)     # columns inactive (high)

    # --- buttons ---
    def _scan_loop(self):
        while self._running:
            matrix = self._read_matrix()
            now = time.monotonic()
            for r in range(4):
                for c in range(2):
                    curr, prev = matrix[r][c], self._prev[r][c]
                    btn = _BUTTON_MAP[r][c]
                    pressed = curr == 0            # active-low
                    if btn in _HOLD_BUTTONS:
                        self._scan_hold_button(btn, pressed, prev == 0, now)
                    elif pressed and prev == 1:    # press edge -> fire immediately
                        self._on_press(btn)
                    self._prev[r][c] = curr
            time.sleep(self.debounce_s)

    def _scan_hold_button(self, btn, pressed, was_pressed, now):
        """Hold-to-fire: light the LED while held, fire the action once at
        _HOLD_S, never re-fire while still held, reset cleanly on release
        (fired or not -- a short tap does nothing but light+unlight the LED)."""
        _, led = _BUTTON_ACTION.get(btn, (None, 0))
        if pressed and not was_pressed:            # press edge: start timing
            self._hold_start[btn] = now
            self._hold_fired.discard(btn)
            with self._lock:
                self._ephemeral_led = led
                self._write_leds_locked(led)
        elif pressed and was_pressed:               # still held
            started = self._hold_start.get(btn)
            if (started is not None and btn not in self._hold_fired
                    and now - started >= _HOLD_S):
                self._hold_fired.add(btn)
                self._on_press(btn)
        elif not pressed and was_pressed:            # release edge: reset
            self._hold_start.pop(btn, None)
            self._hold_fired.discard(btn)
            with self._lock:
                self._ephemeral_led = 0
                self._write_leds_locked(self._desired_led())

    def _read_matrix(self):
        res = [[1, 1], [1, 1], [1, 1], [1, 1]]
        if not self._bus:
            return res
        a = self.mcp.addr
        try:
            for col in range(2):
                col_out = ~(1 << col) & 0x03
                with self._lock:
                    self._bus.write_byte_data(a, self.mcp.GPIOB, col_out | 0xFC)
                    time.sleep(self.mcp.col_settle_s)
                    val = self._bus.read_byte_data(a, self.mcp.GPIOB)
                for row in range(4):
                    bit = (val >> (row + 2)) & 0x01
                    if self.mcp.swap_columns:
                        res[row][1 - col] = bit
                    else:
                        res[row][col] = bit
        except Exception as e:
            self.log("buttons: matrix read error:", e)
        return res

    def _get_button_action(self, btn):
        dflt_action, led = _BUTTON_ACTION.get(btn, (None, 0))
        if self._app is not None and hasattr(self._app, "settings"):
            cfg = self._app.settings.get("buttons", f"btn_{btn}")
            if cfg and isinstance(cfg, dict):
                act = cfg.get("action")
                arg = cfg.get("arg")
                if act:
                    return (act, arg), led
        return (dflt_action, None), led

    def _on_press(self, btn):
        (cmd, arg), led = self._get_button_action(btn)
        self.log("button %d pressed: cmd=%s arg=%s" % (btn, cmd, arg))
        if cmd and cmd != "none":
            try:
                if arg:
                    self.handle(cmd, arg)
                else:
                    self.handle(cmd)
            except Exception as e:
                self.log("buttons: handle error", cmd, e)
        if led:
            self._flash(led)

    # --- LEDs: an animation ticker owns GPIOA -----------------------------------
    # A boot sweep on start (a "powering up" flourish matching the OLED boot),
    # then a steady loop polling the live status: play = play LED solid, pause = a
    # gentle on/off heartbeat (the MCP is digital-only -- no PWM -- so a pulse, not
    # a brightness breath), stopped/idle = all dark. A button press still flashes
    # its own LED for feedback, overriding the ticker for feedback_s.
    def _led_loop(self):
        # Wait for Volumio (or the fallback timeout) before the "power on" sweep
        # -- see signal_ready(). A dark panel during the wait is correct: it is
        # not yet true that the system is ready.
        self._ready.wait(timeout=self.ready_timeout_s)
        for led in LED_SWEEP:                       # boot "power on" sweep
            if not self._running:
                return
            with self._lock:
                self._write_leds_locked(led)
            time.sleep(0.15)  # was 0.055 -- too fast to register per-LED
        with self._lock:
            self._write_leds_locked(0)
        while self._running:
            with self._lock:
                if self._ephemeral_led == 0:
                    self._write_leds_locked(self._desired_led())
            time.sleep(0.08)

    def _desired_led(self, now=None):
        now = time.monotonic() if now is None else now
        st = self.store.get()
        if (st.service or "").strip().lower() in _AIRPLAY_SERVICES:
            return 0                       # AirPlay: status unknowable -> no LED
        status = st.status
        if status == "play":
            return LED_PLAY
        if status == "pause":
            # Once idle (fallen to the clock) or the panel has slept, stop the
            # flashing and hold the pause LED SOLID -- calmer, but still lit so you
            # know it is paused even when the OLED is off. While actively paused on
            # the now-playing screen it keeps the gentle heartbeat.
            resting = self._app is not None and (
                getattr(self._app, "asleep", False)
                or getattr(self._app, "_pause_idle", False))
            if resting:
                return LED_PAUSE
            return LED_PAUSE if (now % 1.4) < 1.0 else 0    # active-pause heartbeat
        return 0                                            # stopped: dark

    def _flash(self, led):
        if self._timer:
            self._timer.cancel()
        with self._lock:
            self._ephemeral_led = led
            self._write_leds_locked(led)
        self._timer = threading.Timer(self.feedback_s, self._unflash)
        self._timer.daemon = True
        self._timer.start()

    def _unflash(self):
        with self._lock:
            self._ephemeral_led = 0
            self._write_leds_locked(self._desired_led())

    def _write_leds_locked(self, value):
        """Write GPIOA only on change. Caller MUST hold self._lock."""
        if value == self._hw_led or not self._bus:
            return
        try:
            # value is the LOGICAL mask; led_byte maps it to the physical GPIOA
            # byte (identity on standard wiring, reversed if mcp.led_reverse).
            self._bus.write_byte_data(self.mcp.addr, self.mcp.GPIOA,
                                      led_byte(value, self.mcp))
            self._hw_led = value
        except Exception as e:
            self.log("buttons: LED write error:", e)
