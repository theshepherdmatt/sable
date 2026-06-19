"""Front-panel buttons + LEDs via the MCP23017 (I2C). Sable's OWN controller --
replaces quadify's buttonsleds daemon. Two wins over the old version: button
presses go through the SAME app.handle as the rotary/IR (one command vocabulary),
and the play/pause LED follows Sable's live StateStore (instant) instead of
shelling out to `volumio status` every 2 seconds.

Hardware contract (hardware.MCP): GPIOA = 7 LEDs (bits 0-6, one lit at a time);
GPIOB = a 2-col x 4-row button matrix (B0/B1 drive the columns, B2-B7 read the
rows with pull-ups). Button 8 + its LED are the unit's HARDWARE power and are
left entirely alone (never scanned, never driven).

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

# Boot "power on" sweep order across all seven LEDs.
LED_SWEEP = [LED_PLAY, LED_PAUSE, LED_PREV, LED_NEXT, LED_SHUFF, LED_REPEAT, LED_SPARE]

# AirPlay: Volumio reports status=play even when paused and gives no reliable
# play/pause signal, so a status LED would lie -- show none for these services.
_AIRPLAY_SERVICES = ("airplay", "airplay_emulation")

# Panel button id -> (Sable command or None, feedback LED). Button 8 (power) is
# hardware-only and never appears here.
_BUTTON_ACTION = {
    1: ("play", LED_PLAY),
    2: ("pause", LED_PAUSE),
    3: ("previous", LED_PREV),
    4: ("next", LED_NEXT),
    5: ("random", LED_SHUFF),
    6: ("repeat", LED_REPEAT),
    7: (None, LED_SPARE),     # spare: LED feedback only
}
# Matrix layout: row -> [col0, col1] button ids (matches the proven wiring).
_BUTTON_MAP = [[1, 2], [3, 4], [5, 6], [7, 8]]


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

    # --- lifecycle ---
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
            for r in range(4):
                for c in range(2):
                    curr, prev = matrix[r][c], self._prev[r][c]
                    btn = _BUTTON_MAP[r][c]
                    if btn != 8 and curr == 0 and prev == 1:   # press edge (active-low)
                        self._on_press(btn)
                    self._prev[r][c] = curr
            time.sleep(self.debounce_s)

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

    def _on_press(self, btn):
        cmd, led = _BUTTON_ACTION.get(btn, (None, 0))
        self.log("button %d pressed" % btn)
        if cmd:
            try:
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
        for led in LED_SWEEP:                       # boot "power on" sweep
            if not self._running:
                return
            with self._lock:
                self._write_leds_locked(led)
            time.sleep(0.055)
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
