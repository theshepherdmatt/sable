"""Buttons + LEDs daemon (MCP23017 on i2c-1 @ 0x20).

Runs as its OWN process (sable-controls). It does NOT open a Volumio connection:
the display process owns the single listener and fans play/pause status here over
the controls command socket (NDJSON: {"cmd":"led_status","arg":"play"}). Buttons
send Volumio commands directly (decoupled).

Carry-forward #4: an early-boot write to 0x20 can EIO until the bus settles, so
open the bus with retry and close() the SMBus handle on stop. The matrix decode
is a pure function so it is unit-testable without an MCP.
"""
import os

from .hardware import LED_PAUSE, LED_PLAY, led_byte

CONTROLS_SOCKET = os.environ.get("SABLE_CONTROLS_SOCK", "/tmp/sable-controls.sock")

# Logical (post-swap) (row, col) -> action. col is 0/1, row 0..3.
BUTTON_MAP = {
    (0, 0): "play",
    (0, 1): "pause",
    (1, 0): "previous",
    (1, 1): "next",
    (2, 0): "random",     # shuffle
    (2, 1): "repeat",
    (3, 0): "spare",      # LED flash only
    (3, 1): "power",      # hardware gpio-shutdown; no software action
}


def decode_column(col, reg_b, rows=4, swap_columns=True):
    """Given the column being driven low and the GPIOB read-back, return pressed
    (row, col) in logical coordinates. Rows are inputs B2..B(2+rows), active-low."""
    pressed = []
    logical_col = (1 - col) if swap_columns else col
    for r in range(rows):
        if ((reg_b >> (r + 2)) & 1) == 0:
            pressed.append((r, logical_col))
    return pressed


def actions_for(pressed):
    """Map decoded presses to Volumio actions, dropping non-actions."""
    out = []
    for rc in pressed:
        action = BUTTON_MAP.get(rc)
        if action and action not in ("spare", "power"):
            out.append(action)
    return out


def status_led_mask(status):
    """GPIOA bitmask for the status LED: play -> PLAY, pause/stop -> PAUSE."""
    if status == "play":
        return 1 << LED_PLAY
    if status in ("pause", "stop"):
        return 1 << LED_PAUSE
    return 0


class ControlsDaemon:
    def __init__(self, mcp, log=print):
        self.mcp = mcp
        self.log = log
        self._bus = None

    def open_with_retry(self, attempts=20, delay=0.5):
        import time

        from smbus2 import SMBus

        for i in range(attempts):
            try:
                bus = SMBus(self.mcp.bus)
                bus.write_byte_data(self.mcp.addr, self.mcp.IODIRA, 0x00)  # GPIOA -> LED outputs
                bus.write_byte_data(self.mcp.addr, self.mcp.IODIRB, 0xFC)  # B0/B1 out, B2..7 in
                bus.write_byte_data(self.mcp.addr, self.mcp.GPPUB, 0xFC)   # pull-ups on inputs
                self._bus = bus
                self.log("controls: MCP open on attempt", i + 1)
                return True
            except OSError as exc:
                self.log("controls: MCP open failed (%s), retrying" % exc)
                time.sleep(delay)
        return False

    def set_status_led(self, status):
        mask = status_led_mask(status)
        self.log("controls: status LED -> %s (GPIOA=0x%02X)" % (status, mask))
        if self._bus is not None:
            try:
                self._bus.write_byte_data(self.mcp.addr, self.mcp.GPIOA,
                                          led_byte(mask, self.mcp))
            except OSError as exc:
                self.log("controls: LED write failed:", exc)

    def close(self):
        if self._bus is not None:
            try:
                self._bus.close()
            finally:
                self._bus = None


def _command_handler(daemon):
    def handle(cmd, arg):
        if cmd == "led_status":
            daemon.set_status_led(arg)
        else:
            daemon.log("controls: ignoring", cmd)
    return handle


def main(argv=None):
    import argparse
    import time

    from .hardware import MCP
    from .ipc import CommandServer

    ap = argparse.ArgumentParser(prog="sable.controls")
    ap.add_argument("--sim", action="store_true",
                    help="no MCP: receive led_status and log it (bench safe)")
    args = ap.parse_args(argv)

    daemon = ControlsDaemon(MCP)
    if args.sim:
        print("controls: SIM mode (no MCP); LED changes logged only")
    elif not daemon.open_with_retry():
        print("controls: could not open MCP after retries; exiting")
        return 1

    server = CommandServer(_command_handler(daemon), path=CONTROLS_SOCKET, log=print)
    server.start()
    print("controls: listening on", CONTROLS_SOCKET)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        server.stop()
        daemon.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
