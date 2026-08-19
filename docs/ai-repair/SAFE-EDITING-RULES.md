# Sable -- Safe Editing Rules for an AI Repair Agent

Read this before making any change to this repository. It defines what
you're allowed to touch, what you must never touch, and how to conclude a
repair.

## Standing instruction: NEVER restart services yourself -- tell the user to REBOOT

After making any change, **do not** run `systemctl restart`, `systemctl
start`, `reboot`, `poweroff`, or any equivalent command yourself, even if
you have the technical ability to (e.g. via a sudoers rule). Instead:

1. Make the file edit(s).
2. Explain in plain English what you changed and why.
3. Tell the user: **"Please reboot the Pi now for this to take effect."**

Why: this repo's own installer follows exactly this pattern deliberately --
`install.sh` explicitly "NEVER reboots or starts the service -- it installs
+ enables, then tells you to reboot" (`install.sh:11`), and ends with `log
"DONE. Nothing was started or rebooted."` (`install.sh:239-241`). A repair
agent working over a Samba share, talking to a non-technical user, has even
less visibility into what else might be running on the Pi at that moment
than the installer does. Let the user control the timing of a reboot/restart
on their own machine.

This applies even to changes that Sable itself can apply "live" without a
reboot (e.g. some `settings.json` keys) -- when in doubt, default to asking
for a reboot; it is always safe, even if occasionally not strictly required.

## Allowed changes

You may edit these, following the guidance in
`docs/ai-repair/CONFIG-FILES.md`:

- **GPIO/wiring mappings for THIS project's own inputs**: rotary encoder
  pins, OLED DC/RST/BLANK pins, MCP23017 I2C address, IR GPIO pin -- via
  `config/hardware.json` (wiring) and `bin/sable-set-ir-pin.sh` +
  `/boot/userconfig.txt`'s Sable-added overlay lines (IR pin specifically).
- **User preferences**: `config/settings.json` -- display theme/brightness/
  rotation, screensaver timings, clock format, IR enable/profile, button
  action assignments, DAC input hint.
- **Branding/theme**: display theme selection (`display.theme`:
  `panel`/`cinema`) and any assets under `assets/` if the user wants visual
  changes, within what the existing screen code supports.
- **Button assignments**: `config/settings.json`'s `buttons.btn_N` entries
  (action + arg per button).
- **IR remote profile**: adding a new profile under
  `config/lirc/profiles/<Name>/lircd.conf` or replacing
  `config/lirc/lircd.conf` for a different remote.
- **Spectrum/CAVA tuning**: bar count/sensitivity in `config/cava.conf` /
  `config/cava-live.conf`, without changing the fifo paths (see
  CONFIG-FILES.md for why).
- **Application source code** (`src/sable/**/*.py`) for genuine bug fixes,
  IF you have read and understood the relevant module and can point to the
  specific defect. Prefer the smallest change that fixes the diagnosed
  issue. Run the relevant test(s) in `tests/` before calling a Python change
  done, where a directly-relevant test exists.

## Forbidden changes

Never do these, regardless of what the user's problem seems to require:

- **Do not install, remove, or upgrade system packages** (`apt`, `pip`
  outside of what `requirements.txt` already lists) yourself. If a
  dependency genuinely seems to be missing, tell the user to re-run
  `install.sh` (it's idempotent) or install the specific package, rather
  than running `apt`/`pip` commands yourself.
- **Do not touch Volumio or moOde core** outside this plugin/project folder.
  This project deliberately treats Volumio/moOde as an external system it
  only talks to over Socket.IO/MPD (`README.md`, `src/sable/volumio/`,
  `src/sable/moode/`) -- it does not and should not reach into Volumio's own
  application code, database, or other plugins.
- **Do not touch network configuration** (Wi-Fi, hostname, SSH, firewall,
  Samba/SMB config beyond what the user explicitly asked about) -- entirely
  outside this project's scope.
- **Do not edit anything outside the project folder** except the two
  specific, narrow, documented exceptions this project's own installer
  already touches: `/boot/userconfig.txt` (kernel overlays -- only the lines
  Sable added/manages) and `/etc/sudoers.d/sable` (only via re-running
  `install.sh`, never by hand). If a fix seems to require touching anything
  else outside the repo, stop and explain that to the user instead of doing
  it.
- **Do not hand-edit `/etc/sudoers.d/sable`.** Always regenerate it by
  re-running `install.sh`, which validates the file with `visudo -cf`
  before installing it (`install.sh:139-144`). A malformed sudoers file can
  lock out sudo entirely.
- **Do not run destructive commands**: no `rm -rf`, no `git reset --hard`,
  no `git checkout -- .`, no `git clean`. This repo's own working tree
  typically carries uncommitted changes -- treat that as normal, not as
  something to clean up. If you need git history, read it; do not rewrite
  or discard it.
- **Do not re-enable `sable-boot-splash.service`.** It is intentionally
  installed but disabled (`install.sh:204-219`) because it causes the
  cold-boot blank-panel bug described in
  `docs/ai-repair/COMMON-FIXES.md`. Never turn it back on.
- **Do not edit `plugin/sableapp/`** as if it were the source -- it's a
  build output mirrored by `tools/build-plugin.sh`. Edit the top-level
  `src/`, `config/`, etc. and rebuild instead, or your fix will be silently
  discarded on the next plugin refresh.

## When you're not sure

If a fix requires touching something not covered above, or requires a
change whose blast radius you're not fully confident about, stop and
explain the situation to the user in plain English rather than guessing.
It is always acceptable to say "I found the likely cause but the fix
touches something outside what I'm confident is safe to change -- here's
what I found and what I'd suggest."
