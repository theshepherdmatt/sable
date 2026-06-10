# Sable -- STATUS / handoff (2026-06-10, post-hardware)

Recovery + handoff doc. Per-phase detail also lives in Claude memory
(`quadify-project-state.md`, auto-loaded each session). The CODE runs on the Pi
at `/home/volumio/sable` (source of truth) and is mirrored at
`C:\Users\Matt\sable-stage` on the PC.

## What Sable is
Ground-up successor to the Quadify-Evo plugin: the front-panel controller (OLED +
rotary + IR + buttons/LEDs + spectrum) for the Audiophonics EVO Sabre (USB-XMOS
DAC) in a Quad case, on RPi4 / Volumio / Bookworm. Volumio is the player; Sable is
the display/control layer. Built ALONGSIDE the still-installed `quadify` plugin,
whose conflicting services are retired but whose `cava` binary + LIRC config are
still used.

## Status: LIVE ON HARDWARE as a boot service.
`sable.service` runs `python3 -u -m sable.app --hardware --stage full` (user
volumio, `Restart=always`), enabled on boot, confirmed surviving cold reboot.
Sable now owns OLED + rotary + IR + buttons + LEDs + spectrum, all native.

## Build/run workflow
Edit in `C:\Users\Matt\sable-stage` -> `scp` the changed file to
`volumio:~/sable/<same path>` -> `sudo systemctl restart sable.service` ->
`journalctl -u sable.service -b 0 -f`. Validate before restart with
`python3 tools/ascii_guard.py <files>` + `python3 -m py_compile <files>` +
`for t in tests/test_*.py; do PYTHONPATH=src python3 $t; done` (23 tests).
NOTE: the PC's tools are rooted on the `\\volumio.local\Quadify` Samba share, so
while the Pi reboots, ALL Bash/PowerShell calls fail (dead CWD) -- you go dark
until it's back; that's expected, not a tool bug.

## sudo on this Pi (important)
`sudo systemctl / mv / rm / tee / mkdir / ln / chmod` are NOPASSWD, but
`sudo cp` is NOT (a later `(ALL) ALL` sudoers line shadows it -> password
prompt). Install files into root dirs via `sudo tee < src > /dev/null` or
`sudo mv`, never `sudo cp`.

## Phases / work done (all verified on hardware unless noted)
- **Phase 0-3** (SIM only): scaffold + boot/clock/rotary slice; single Volumio
  Socket.IO listener (EIO3, socketio 4.6.1) + immutable StateStore; ModernScreen
  with shared render tick + marquee + album art + PNG icons; ONE configurable
  spectrum MeterScreen + shared CAVA fifo reader/smoother.
- **Real-OLED bring-up**: `OledDisplay` (SSD1322 over SPI) now executes; staged
  `--stage clock|modern|spectrum|full`; rotate=0 confirmed right-side-up.
- **Real-music spectrum**: Sable's own cava (`config/cava-live.conf`) on MPD's
  `/tmp/cava.fifo` -> `/tmp/sable-display.fifo` (24 bars). Replaces cava.service.
- **Menu**: data-driven nestable tree (`screens/menu.py`): Music / Now Playing /
  Display Mode (Modern/Spectrum Bars/Spectrum Dots) / Brightness. Scroll clamps.
- **Boot service + installer**: `systemd/sable.service` + `install.sh` (idempotent).
- **IR**: Sable's own in-process listener (`inputs/ir.py`) on `/run/lirc/lircd`,
  ApEvo remote, D-pad mapping (OK/RIGHT=select, LEFT=back, UP/DOWN=scroll,
  MENU=menu, NEXT/PREV=transport, INPUT=dac_input). `run_hardware` builds the App
  with `dry_run=False` and also starts the IPC `CommandServer` (`/tmp/sable-cmd.sock`).
- **Buttons + LEDs** (`inputs/buttons.py`): drives MCP23017 (I2C 0x20) directly;
  ported from quadify's matrix scan but presses go through `app.handle` and the
  play/pause LED follows the live StateStore. Button 8 + its LED = HARDWARE power,
  never touched.
- **Screensaver**: `App.tick_idle` sleeps the OLED after `screensaver.idle_s`
  (default 3600s) when idle+stopped; any input/playback wakes it. (Animated
  geo/snake tier NOT built; collapses to clock-idle -> panel-off.)
- **Focused browser** (`screens/browse.py`): Favourites / Playlists / Radio,
  drill folders via `listener.browse(uri)`, play leaves via `listener.play_item`
  (`replaceAndPlay` -- the ONE place Sable initiates playback). Menu "Music" opens it.
- **FSM stuck-on-clock fix**: screen switch was purely edge-triggered, so a play
  edge fired while in menu/browse/splash was dropped -> stuck on clock with audio
  playing. Added `App.reconcile_screen()` (every render tick: clock+playing ->
  now-playing), `base_screen()` for menu/browse exits, now-playing select opens
  the menu, removed the redundant "Clock" menu item.
- **Cold-boot blank-screen fix**: `OledDisplay.__init__` now pulses BCM25 (the
  panel reset/blank line) LOW->HIGH before luma init -- a cold boot left it
  already-high (no reset), so the panel stayed dark; a restart masked it.

## CURRENT RUNTIME STATE (audited 2026-06-10)
- `sable.service`: **active + enabled**. Procs: `sable.app --hardware --stage full`
  + its `cava ... cava-live.conf`.
- **Retired** (unit files MOVED to `~/sable/disabled-units/`, because the quadify
  plugin re-enables them on boot so `disable` alone does not stick):
  `quadify.service`, `cava.service`, `quadify-buttonsleds.service`,
  `ir_listener.service`.
- **Kept active** (Sable depends on / harmless): `lircd.service` +
  `quadify-lirc-post.service` (Sable's IR needs the LIRC socket); `early_led8.service`
  (one-shot early-boot LED indicator on the MCP -- harmless, Sable re-inits the MCP).
- The **quadify plugin is still installed** -- do NOT uninstall it yet: Sable uses
  its `cava` binary (`/data/plugins/system_hardware/quadify/cava/bin/cava`) and the
  LIRC setup. Bundling cava into Sable would remove this last coupling.
- `~/sable` is **NOT a git repo** -- no version control yet.
- MCP23017 hardware fault (the old `[Errno 5]` at 0x20) was **fixed by the user**
  (rewiring) 2026-06-10; buttons + LEDs work.

## Deferred / TODO
1. **Version control** -- get `~/sable` into git (only the Pi + PC mirror exist).
2. **All-source spectrum** -- only MPD-routed audio feeds bars; Spotify/AirPlay/
   rp2 bypass MPD. `snd-aloop` is NOT on this kernel; an ALSA `type file` tee
   risks stalling audio. Needs bench care. (rp2 also was flaky on Evo -- don't test with it.)
3. **Bundle the cava binary** into Sable (drop the quadify dependency).
4. **Volumio web-UI <-> settings bridge** (Node plugin) so settings.json is
   editable from the web app.
5. **Animated screensaver tier** (geo/snake before full sleep).
6. **Full-stack cold-boot certification** -- reboot and confirm screen +
   buttons/LEDs + rotary/IR/spectrum all come up unattended (display fix verified;
   buttons/LEDs-on-boot not yet certified across a reboot).

## Key gotchas (don't relearn these)
- Plugin RESURRECTS retired services on boot -> MOVE unit files, don't just disable.
- `/tmp/cava.fifo` is single-reader -> only one cava may run (why cava.service is retired).
- BCM25 needs a LOW->HIGH *pulse* at init, not just HIGH, or the panel stays blank.
- rotate=0 is correct for this panel (config.yaml's rotate:180 was a stray).
- lircd broadcasts to all clients (so listeners coexist), but the duplicate quadify
  ir_listener was retired anyway.
