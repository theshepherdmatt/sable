# Sable

Front-panel controller for the Audiophonics EVO Sabre (Quad case) on Volumio 4.
Ground-up successor to Quadify-Evo. See `DESIGN.md` for the architecture and
`STATUS.md` for where the build currently stands and what is next.

The code RUNS on the Pi at `/home/volumio/sable`. A PC-side staging mirror at
`C:\Users\Matt\sable-stage` exists only as an authoring/transfer buffer (edit
there, `scp` to `~/sable`, run on the Pi). Nothing executes on the PC.

NOT deployed. Verified entirely on a `SimDisplay` backend (PNG frames + inline
ASCII previews) so it never touches the live `quadify` plugin's hardware. The
real-OLED backend (`--hardware`) is refused in this build.

## Layout
```
src/sable/
  hardware.py        frozen hardware contract (pins, I2C addr, LED bits, DAC labels)
  settings.py        ONE source of truth (config/settings.json), atomic writes
  clock_gate.py      NTP boot gate
  state.py           PlayerState + StateStore (Volumio quirks normalized here)
  ipc.py             command socket (server + send)
  fsm.py             small declarative state machine (state == screen)
  app.py             wiring + verification harnesses (demo / modern / spectrum)
  controls.py        buttons/LEDs daemon (MCP23017) + LED fan-out receiver
  ir.py              IR listener (LIRC) + mode-aware key translate
  volumio/listener.py  the single Socket.IO listener (EIO3, queries only)
  display/  base / sim / oled(real, unused) / fonts / albumart / icons / fifo_meter
  screens/  base / splash / clock / menu / modern / meter
  inputs/   rotary (pure decoder + real driver) / sim_input
config/     settings (generated) + cava.conf (test/standalone)
assets/icons/  pre-rendered PNGs (+ src/*.svg) -- no Cairo at runtime
systemd/    one-template-per-service units (.tmpl, not installed)
node/       Volumio plugin controller (index.js, ASCII-clean) + package.json
tools/      ascii_guard / render_icons / test_tone
tests/      pure self-tests (no hardware) + fixtures/rp_paused.json
```

## Run the SIM verification (safe alongside the live plugin)
```
bash bin/run-slice.sh                          # boot -> NTP gate -> clock -> rotary/menu
PYTHONPATH=src python3 -m sable.app --modern-fixture    # modern screen from real RP capture
PYTHONPATH=src python3 -m sable.app --modern-live       # modern from live Volumio (read-only)
PYTHONPATH=src python3 -m sable.app --spectrum-fixture  # synthetic spectrum sweep + decay
for t in decoders state modern meter; do python3 tests/test_$t.py; done   # 23 tests
PYTHONPATH=src python3 tools/ascii_guard.py node/index.js $(find src -name '*.py')
```

## Real CAVA chain (test source, NOT MPD)
```
mkfifo /tmp/sable-cava.fifo /tmp/sable-display.fifo
python3 tools/test_tone.py > /tmp/sable-cava.fifo &
/data/plugins/system_hardware/quadify/cava/bin/cava -p config/cava.conf &
PYTHONPATH=src python3 -m sable.app --spectrum-live --seconds 5
# then: kill the two bg procs; rm the two fifos
```

## Real hardware (OLED/rotary/buttons)
`--hardware` is intentionally refused while the live plugin runs. A real-OLED run
requires the live `quadify` plugin stopped (no SPI/I2C/GPIO contention) and
enabling the `OledDisplay` path. `oled.py` has not yet been run on hardware.
