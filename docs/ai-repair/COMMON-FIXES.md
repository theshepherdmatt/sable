# Sable -- Common Fixes (Symptom -> Cause -> Fix)

Each entry is grounded in actual code/comments/tests in this repo. When
adding new entries, keep the same three-part structure so this file stays
easy for an AI agent to scan.

---

## Rotary encoder not responding after a DAC HAT swap

**Symptom:** Turning the rotary knob does nothing (no scroll, no volume
change); the front-panel buttons and IR may still work fine. This started
after installing or swapping a DAC HAT. (Front-panel buttons/LEDs staying
unaffected is expected and confirms the conflict is GPIO-specific to the
rotary: buttons/LEDs run entirely over I2C through the MCP23017, not on
direct GPIO pins, so a DAC HAT's GPIO usage cannot touch them -- see
`docs/ai-repair/GPIO-MAP.md`. If buttons/LEDs ALSO broke at the same time,
suspect a broken I2C bus instead, e.g. via `i2cdetect -y 1`.)

**Cause:** Sable's rotary encoder uses BCM **GPIO13 (CLK), GPIO5 (DT),
GPIO6 (SW)** by default (`src/sable/hardware.py:33-36`). Some HAT boards
that stack directly on the 40-pin header can end up sharing or shadowing
these pins depending on how they're wired. Sable's own IR pin is
**GPIO27** as of 2026-08-19 (moved from GPIO4 to match quadify.uk's
published wiring diagram) -- an older install that hasn't been
re-installed/rebooted since that change may still have IR on GPIO4; check
`/boot/userconfig.txt`'s `dtoverlay=gpio-ir,gpio_pin=N` line rather than
assuming (see `docs/ai-repair/GPIO-MAP.md`, "Known DAC HAT conflicts"). A
HAT driving one of these pins electrically will corrupt the rotary's
quadrature reads.

**Fix:**
Run the full diagnostic procedure in **`docs/ai-repair/DAC-CHECKER.md`** --
it walks through identifying the DAC HAT's active overlay, finding its pin
usage (from GPIO-MAP.md if already documented, or externally if not), and
remapping this project's own GPIOs if they collide. Summary:
1. Confirm which pins the new DAC HAT actually uses (check the HAT
   vendor's documentation or overlay -- this repo does not know that).
2. Compare against `docs/ai-repair/GPIO-MAP.md`'s pin table.
3. If there's a genuine overlap, re-wire the rotary encoder to different
   free GPIOs and override them in `config/hardware.json`:
   ```json
   { "rotary": { "clk": 26, "dt": 16, "sw": 12 } }
   ```
   (pin numbers are examples -- pick pins the DAC HAT does not use).
4. If the IR pin is the conflict, either disable IR
   (`"ir": {"enabled": false}` in `config/settings.json`) or move it with
   `bin/sable-set-ir-pin.sh <new-bcm-pin>` (edits
   `/boot/userconfig.txt`'s `dtoverlay=gpio-ir,gpio_pin=` line -- requires
   reboot to take effect).
5. Tell the user to **reboot the Pi** (GPIO/overlay changes need a fresh
   boot; `config/hardware.json` changes need at minimum a
   `sudo systemctl restart sable.service`, but a reboot is the safe,
   simple instruction to give a non-technical user in all cases -- see
   `docs/ai-repair/SAFE-EDITING-RULES.md`).

---

## Spectrum / VU meter goes flat / stuck mid-session (Volumio)

**Symptom:** The spectrum or VU visualiser was working, then freezes at
all-zero bars while music is still audibly playing.

**Cause:** CAVA (the spectrum analyser process Sable spawns) can desync
internally -- it keeps running and keeps writing well-formed frames on
schedule, but every value is stuck at 0. This is documented in detail in
`src/sable/display/fifo_meter.py:79-89` and pinned by
`tests/test_cava_recovery.py`. It is distinct from CAVA crashing outright.

**Detection/fix (already automatic, no manual repair needed in normal
operation):** `FifoBars.read()` (`src/sable/display/fifo_meter.py:114-175`)
tracks how long the feed has read all-zero. After `stuck_after_s` (15s by
default) of solid zeros **while something is actually playing**, it calls
`on_stuck`, wired to `App.respawn_cava()` (`src/sable/app.py:775`), which
kills and relaunches the CAVA process. This is re-armable (fires again on a
later stall, not just once per session) and only fires when
`store.get().status == "play"` (a stopped/paused player legitimately has
all-zero bars, so respawning then would do nothing and just churn a
process).

**If it's still not recovering:**
1. Check the log for `cava: respawning (...)` / `cava: respawn failed` --
   `journalctl -u sable.service -f`.
2. Confirm `cava` is actually installed: `which cava` (installed via
   `apt install cava` in `install.sh:55`).
3. Confirm MPD is actually feeding `/tmp/cava.fifo` -- `install.sh:147-184`
   adds this MPD output; if it's missing (e.g. Volumio regenerated
   `mpd.conf` and dropped it), re-run `install.sh`.
4. Remember: **AirPlay, Spotify, and radio-via-own-player sources bypass
   MPD entirely** (`_NON_CAVA_SERVICES` in `src/sable/app.py:74-77`) so
   they never feed CAVA -- a flat spectrum on those sources is correct
   behaviour, not a bug. Test with a local file or standard web radio
   instead.

---

## No spectrum / VU needles on moOde

**Symptom:** On a moOde install, the visualiser screen shows flat bars or a
still needle, even though music is audibly playing.

**Cause:** moOde uses **peppyalsa**, not CAVA, for its spectrum feed
(`src/sable/display/fifo_meter.py:18-39`, `README.md:111-113`). moOde ships
peppyalsa's config as `peppy.conf.hide` and only renames it to `peppy.conf`
once **peppyalsa is switched on in moOde's own web UI** -- this is OFF by
default. Until then, `/tmp/peppyspectrum` exists as a pipe but nothing ever
writes to it.

**Fix:** This cannot be fixed by editing Sable's code or config -- it is a
moOde system setting. Tell the user to enable peppyalsa in moOde's own UI.
(`README.md:149-155` documents this exact symptom and fix.) Confirm with:
```
ls /etc/alsa/conf.d/peppy.conf
```
If only `peppy.conf.hide` exists, peppyalsa is still off.

---

## Power button (button 8, hold) does nothing

**Symptom:** Holding the power/shutdown button on the front panel for 2+
seconds does not power off the Pi.

**Cause (historical bug, now fixed by a settings migration, but worth
checking on an old/imported settings.json):** `config/settings.json` used to
ship button 8 as `{"action": "none"}`, which is truthy and therefore
overrode the built-in `"shutdown"` default in `_BUTTON_ACTION`
(`src/sable/inputs/buttons.py:42-51`, `_get_button_action` at line 243).
Fixed by a settings migration in `src/sable/settings.py:94-116` (rev 2),
which rewrites a broken `btn_8: "none"` to `"shutdown"` on load -- but ONLY
if it was exactly `"none"`/empty; a deliberate reassignment is left alone.

**Fix:**
1. Check `config/settings.json` -> `buttons.btn_8.action`. It should read
   `"shutdown"` unless the user deliberately reassigned it.
2. If it's stuck on `"none"` and the migration didn't apply (e.g. `_meta.rev`
   was already >= 2 when the bad value was written), manually set it back:
   ```json
   "buttons": { "btn_8": { "action": "shutdown", "arg": "" } }
   ```
3. Also confirm the MCP23017 is actually detected: `i2cdetect -y 1` should
   show a device at `0x20` (or whatever address it auto-probed to).
4. If poweroff itself fails after the button correctly fires `"shutdown"`,
   see the next entry.

---

## Shutdown message shows but the Pi never actually powers off

**Symptom:** Holding the power button shows "SHUTTING DOWN / please wait",
the screen blanks, but the Pi stays running indefinitely.

**Cause:** `App._poweroff()` (`src/sable/app.py:684-736`) first tries
emitting a `shutdown` Socket.IO event to Volumio (Volumio's own web power
button does the same thing) and waits `_VOLUMIO_POWEROFF_GRACE_S` (6s). A
successful `emit()` does NOT guarantee Volumio acted on it -- on some
Volumio builds it's silently ignored. Sable then falls back to trying
several `poweroff`/`systemctl poweroff` command variants, in order, via
`sudo`. **This whole fallback chain depends on the sudoers rule installed by
`install.sh` (section 3c) at `/etc/sudoers.d/sable`.** If that file failed
`visudo` validation at install time, it is NOT installed, and every `sudo`
poweroff attempt is refused.

**Fix:**
1. Check the log for `shutdown error: no way to power off ... check
   /etc/sudoers.d/sable` (`src/sable/app.py:734-736`).
2. Confirm the file exists: `sudo cat /etc/sudoers.d/sable` -- should list
   `SABLE_CMDS` including `/bin/systemctl poweroff` and `/sbin/poweroff`
   with `NOPASSWD`.
3. If missing, re-run `install.sh` (it regenerates and validates this file
   on every run, section 3c, `install.sh:113-145`).
4. If it exists but permissions are wrong, it must be `root:root`, mode
   `0440` (`install.sh:140`).

---

## Panel is blank / garbled after a fresh boot (but fine after a manual restart)

**Symptom:** After a cold power-on, the OLED stays completely dark (or
shows corrupted/ghosted pixels), but `sudo systemctl restart sable.service`
immediately fixes it.

**Cause:** Extensively documented in `src/sable/display/oled.py:52-90` and
`STATUS.md:68-70,110`. The SSD1322 controller is write-only (no readback),
and its GPIO25 reset/blank line can power up already-HIGH on a cold boot --
so a simple "set it HIGH" init is a no-op and the panel never actually
resets. `OledDisplay.__init__` now pulses the line LOW then HIGH
deliberately (not just sets it) to force a real reset. A related, now-fixed
issue was `sable-boot-splash.service` initialising the SSD1322 before
`sable.service` did and leaving it in a state Sable's own init couldn't
recover -- this service is intentionally installed but **disabled**
(`install.sh:204-219`) and must stay that way.

**Fix (if still occurring despite the above being in place):**
1. Confirm `sable-boot-splash.service` is NOT enabled:
   `systemctl is-enabled sable-boot-splash.service` should say `disabled`.
   If it says `enabled`, run `sudo systemctl disable sable-boot-splash.service`.
2. Confirm nothing else is holding SPI0 (e.g. the old `quadify.service` --
   `run_hardware()` already refuses to start if that's active,
   `src/sable/app.py:1065-1070`, but check anyway:
   `systemctl is-active quadify.service`).
3. If the wiring is a non-standard unit (e.g. EVO Sabre), confirm
   `config/hardware.json` has the correct `oled.dc`/`oled.rst`/`oled.blank`
   values for that physical board.
4. Tell the user to power-cycle (not just restart the service) to verify --
   this exact fault only reproduces on a cold boot, per `STATUS.md:110`.

---

## IR remote does nothing

**Symptom:** Pressing buttons on the IR remote has no effect; front panel
buttons and rotary work fine.

**Cause:** Several independent possibilities, all covered in
`README.md:235`:
- `lircd.service` isn't running.
- The `gpio-ir` overlay (`dtoverlay=gpio-ir,gpio_pin=27` by default as of
  2026-08-19, `install.sh:83`) hasn't taken effect because the Pi hasn't
  been rebooted since install.
- `ir.enabled` is `false` in `config/settings.json`
  (`src/sable/app.py:1235-1241` skips starting `IrListener` entirely if so).
- Using a remote whose button codes don't match the loaded LIRC profile.

**Fix:**
1. `systemctl is-active lircd` -- should be `active`.
2. `ls /dev/lirc0` -- only exists after the overlay + a reboot.
3. Run `irw` while pressing remote buttons -- should print `KEY_*` names. No
   output = the remote/receiver/overlay problem, not Sable's code.
4. Check `config/settings.json` -> `ir.enabled` is `true`.
5. If using a non-default remote, check `ir.profile` matches a directory
   under `config/lirc/profiles/`, or replace `config/lirc/lircd.conf`
   directly and re-run the installer (`README.md:177-178`).
