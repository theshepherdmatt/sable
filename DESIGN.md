# Sable -- Design Proposal (Phase 0)

Sable is a ground-up successor to Quadify: a front-panel display + controller
(SSD1322 OLED + rotary + buttons/LEDs + IR) for Volumio 4 / Bookworm on a Raspberry
Pi 4. Volumio stays the player; Sable owns the display + controls.

## Why a rebuild (what we are fixing)
The old code worked but fought itself: a ~1100-line `ModeManager` god object, the
same setting stored in four desynced places, copy-pasted per-screen state
threads, two parallel event systems, file-writes + blind `systemctl restart` as
IPC, and a latent `coerceHexAddr` ReferenceError. Sable keeps the *hard-won
knowledge* (below) and discards the *structure*.

## Process / service topology
Four processes, each owning exactly one hardware resource -- no sharing, no
contention:

| Process | Owns | Responsibility |
|---|---|---|
| `sable-display` (Python) | SPI/OLED + rotary GPIO | render loop, FSM, Volumio listener, IPC server |
| `sable-controls` (Python) | I2C/MCP23017 | buttons -> `volumio` commands; status LED; resilient retry |
| `sable-ir` (Python) | LIRC socket | decode keys -> IPC commands |
| `index.js` (Node) | Volumio plugin API | lifecycle + settings UI only |

The split mirrors the old design (which was actually sound) but with one
*documented* IPC channel instead of an ad-hoc mix.

## IPC -- one channel
A single newline-delimited JSON Unix socket, `/tmp/sable-cmd.sock` (`ipc.py`).
The display app is the server; IR, controls, Node, and a `sablectl` debug CLI are
clients. One vocabulary: `scroll`, `select`, `back`, `menu`, `home`, `toggle`,
`play`/`pause`/`next`/`previous`/`random`/`repeat`, `dac_input`, `volume_*`,
`seek_*`, `skip_*`, `shutdown`, `reload_config`. This replaces file-writes +
blind restarts + event_bus/Blinker.

## Config -- one source of truth
- **Hardware contract** lives in code as frozen dataclasses (`hardware.py`):
  pins, I2C address, SPI bus, LED bits, DAC input labels. It is NOT user-editable
  and never appears in the UI. (This deletes the entire int-`32` / `"20"` /
  `"0x20"` normalization mess -- the MCP address is a constant.)
- **User preferences** live in ONE JSON file, `config/settings.json`
  (`settings.py`), written atomically. The Volumio UI is a **read-only mirror**:
  `getUIConfig` renders from it; `setUIConfig` writes it back and pings the app
  to `reload_config`. No v-conf-as-truth, no four-store drift.

## State model
A small FSM (`fsm.py`) where **a state IS a screen** (`screens/base.py`). One
`Screen` base with `on_enter/on_exit/render/handle_scroll/handle_select/
handle_back/on_state/tick`; every screen and menu is a subclass. The FSM owns the
cross-cutting timers and releases locks before firing transitions (carry-forward
#8). Planned states:

```
                 play push
   splash --gate--> clock <----------> playback (style: modern|vu|digitalvu|
              (NTP)   |  ^  stop/pause    |        minimal|original|webradio)
                      |  |  (1.5s)        |
            idle 3600s|  |                | select
                      v  | wake (input)   v
                 screensaver           library / menu (config|clock|
                      |                  ^   screensaver|system)
            sleep_s   |                  | 15s inactivity
                      v                  |
                  sleep (OLED off) ------+
```

Timings preserved from the contract: menu->clock 15s, pause/stop->clock 1.5s,
mode-switch debounce 0.5s, idle->screensaver ~3600s (pref), screensaver->sleep
(pref). Menus are data-driven instances of ONE `MenuScreen`, not a class each.

## Display + input abstraction
`Display` interface (`display/base.py`) with `OledDisplay` (luma SSD1322, real)
and `SimDisplay` (PNG + ASCII preview, no hardware). Screens are backend-blind.
Inputs are likewise split into a pure decoder (unit-testable) + a real driver:
`rotary.py` has `QuadratureDecoder`; `controls.py` has `decode_column`. This is
what lets Phase 0 verify input logic without touching the live GPIO/I2C.

## dac_input -- corrected design
The Pi has **no control or read-back** of the DAC (confirmed: XMOS exposes only
audio-class interfaces, no HID; user confirms the remote is the only control).
The remote INPUT button switches the DAC's physical input; the *same* NEC code is
also seen by our IR receiver, so we advance a label in lockstep. The persisted
`dac.input_index` is therefore a **hint, not ground truth**, and the design makes
it **user-correctable** (a menu entry to set the shown label to reality after a
missed press or restart). No audio path is ever touched. This fixes the old
write-only/desync behaviour.

## Carry-forward (do NOT rediscover) -- where each lives
1. NTP boot gate (`clock_gate.py`): `ntpq rv 0 stratum` 1..15, fallbacks
   timedatectl then year>=2024; hold splash up to 45s.
2. Deep-sleep via luma `hide()/show()` (0xAE/0xAF), framebuffer preserved
   (`display/oled.py`, `display/base.py`).
3. Dual-pin OLED reset: RST=GPIO24 runtime + GPIO25 low ~1s on stop
   (`display/oled.py` cleanup; `ExecStop` in the unit template).
4. MCP EIO at boot -> resilient retry daemon + `close()` on stop
   (`controls.py`, `sable-controls.service.tmpl` Restart=always).
5. Socket.IO EIO3 pin 4.6.1 / engineio 3.14.2 (`requirements.txt`).
6. irexec neutralisation (installer task next pass; noted in `ir.py`).
7. Atomic writes everywhere (`settings.py`, `index.js`).
8. Release locks before FSM transitions (`fsm.py`).
9. CAVA pipeline MPD->/tmp/cava.fifo->CAVA->/tmp/display.fifo (screens next pass).
10. Install strategy: no venv, `--break-system-packages`, dpkg self-heal,
    NodeSource-repo removal before apt update (install script next pass).
11. Volumio Bookworm `package.json` schema (`plugin/package.json`).

## Dropped (not ported)
System Update/Rollback menu; duplicate screensaver menu; all `.bak`/`__pycache__`
/legacy `/home/volumio/Quadify` paths; dead index.js helpers; the dev-box pip
freeze (this `requirements.txt` is ~7 lines, all imported); the MCP-address UI
field and its hex-normalization code.

## Divergences from the old design (and why)
- State == Screen, one base class (was: god object + duplicated threads).
- Hardware constants in code, prefs in one JSON (was: 4 stores, pins mixed in).
- Documented IPC vocabulary (was: files + blind restart + 2 event systems).
- No DAC-input loop attempt; correctable hint instead (was: open-loop, silent
  desync) -- forced by the hardware, now made honest.

## Phase 1 -- Volumio integration (built)
- **One listener** (`volumio/listener.py`), owned by the display process. EIO3
  via socketio 4.6.1. Subscribes pushState / pushTrack / pushBrowseLibrary /
  pushBrowseSources / pushToastMessage / volume. Emits getState /
  getBrowseSources / browseLibrary as QUERIES only -- it never emits a
  playback- or volume-changing command.
- **Single-flight reconnect**: only the listener's `_run` thread reconnects
  (`reconnection=False` on the client), backoff `min(2*attempt, 60)`. A socket
  bounce cannot spawn competing reconnect threads.
- **State model** (`state.py`): immutable `PlayerState` + `StateStore` with
  change subscribers. Partial pushState merges over the previous state so a
  transient null never blanks the screen. Screens/FSM read `StateStore.get()`;
  they never see a raw payload.
- **Fan-out**: the display pushes status to sable-controls for the LED. Each
  process that needs inbound IPC owns its own socket, all under `/tmp/sable-*`:
  display = `/tmp/sable-cmd.sock`, controls = `/tmp/sable-controls.sock`. Shared
  command vocabulary (`ipc.py`). LED message: `{"cmd":"led_status","arg":"play"}`
  (arg in play|pause|stop). sable-controls opens NO Volumio connection -- one
  connection, fanned out. (Divergence from the brief's wording "over
  /tmp/sable-cmd.sock": a per-process inbound socket is cleaner than making the
  display relay to itself; documented here as DESIGN allows.)

## Socket topology (current)
| Socket | Server (listens) | Clients (connect) | Direction / messages |
|---|---|---|---|
| `/tmp/sable-cmd.sock` | sable-display | sable-ir, Node index.js, `sablectl` | inbound commands to the UI: scroll/select/back/menu/home/toggle/transport/dac_input/shutdown/reload_config |
| `/tmp/sable-controls.sock` | sable-controls | sable-display (listener fan-out) | state push for LEDs: `{"cmd":"led_status","arg":"play|pause|stop"}` |
| Volumio Socket.IO `:3000` | Volumio | sable-display ONLY | one read-only listener; queries getState/getBrowseSources/browseLibrary |
All Sable runtime artifacts are namespaced `/tmp/sable-*`. The live plugin's
`/tmp/quadify.sock`, `/tmp/cava.fifo`, `/tmp/display.fifo`, `/tmp/quadify_mode`
are never touched.

## Phase 2 -- now-playing "modern" screen (built)
- **Normalization in the state layer** (`state.py`), not the screen: stream/mute
  -> bool; seek is canonical ms, duration canonical s; `elapsed_s` / `progress()`
  expose consistent units; `live_position_ms()` extrapolates between pushState
  events so the seek bar advances on the render tick.
- **Shared mechanics in the Screen base** (`screens/base.py`): ONE render tick
  (driven by the app, no per-screen thread) and ONE marquee (`draw_text_clipped`
  + time-based `marquee_offset`, clipped via a strip paste so it never bleeds).
  Screens never reimplement scroll or _read_fifo.
- **ModernScreen** (`screens/modern.py`) reads only `StateStore.get()` +
  `progress_fraction()`. Album art via `display/albumart.py` (stdlib urllib,
  fetched off-tick, cached, placeholder until ready; handles absolute URLs and
  Volumio-relative `/albumart?...`). 
- **Icons**: pre-rendered PNGs blitted via Pillow (`display/icons.py`). PNGs are
  generated at dev time by `tools/render_icons.py` from the SVG sources in
  `assets/icons/src/`; runtime and install need NO Cairo (this is why CairoSVG is
  absent from requirements). The dev script currently draws the glyphs with PIL
  primitives so even dev is Cairo-free; swap to cairosvg there only if exact SVG
  fidelity is ever needed.
- **FSM** (`fsm.py`): declarative `TABLE`. clock --play--> `@nowplaying` (resolves
  to modern|spectrum via `app.nowplaying_screen()`); modern/spectrum
  --stop|back|select--> clock; clock --menu--> menu; menu --back|timeout--> clock.
  No unregistered transitions; every target is a registered screen.

## Phase 3 -- CAVA spectrum / VU meter (built; verified with a TEST source)
- **Shared FIFO reader + smoother** (`display/fifo_meter.py`): ONE `FifoBars`
  (the old code copy-pasted _read_fifo into modern/vu/digitalvu) + ONE
  `BarSmoother` (attack/decay parameterized). CAVA output format consumed: raw
  ASCII, ';'-delimited bars, '\n'-delimited frames, each value 0..255, `bars`
  per frame (matches the existing cava fork's config).
- **ONE meter screen** (`screens/meter.py`): `MeterScreen`, configurable by
  `style` (bars|dots) and `bars` count -- replaces the old vu_screen +
  digitalvu_screen duplication. `feed()` allows headless synthetic-bar tests.
- **Sable's own CAVA plumbing**: `config/cava.conf` reads `/tmp/sable-cava.fifo`
  and writes `/tmp/sable-display.fifo` ONLY. `tools/test_tone.py` is a
  self-contained s16le sweep used as the TEST source. The real MPD->CAVA FIFO
  injection (mpd.conf.tmpl) is DEFERRED to the installer phase. Sable never
  touches the live `/tmp/cava.fifo`, `/tmp/display.fifo`, or mpd.conf.
- **Hardening**: album art has a fetch timeout + bounded cache (FIFO eviction);
  `live_position_ms()` freezes on pause and clamps at duration.

## Run modes (all SIM unless noted)
`python3 -m sable.app` with: `--demo` (Phase 0 boot+input slice),
`--modern-fixture` / `--modern-live`, `--spectrum-fixture` / `--spectrum-live`,
or no flag (interactive: IPC socket + stdin). `--hardware` is REFUSED in this
build (would contend with the live plugin for SPI/I2C/GPIO). The `*-live` modes
connect read-only to Volumio; the spectrum chain needs a separate cava instance
on the sable fifos (see STATUS.md). `bin/run-slice.sh` runs `--demo`.
