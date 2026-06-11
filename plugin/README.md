# Sable settings bridge (Volumio plugin)

A small Volumio plugin that lets you edit the **Sable** OLED front-panel settings
from the Volumio web UI (Settings -> Plugins -> Sable), instead of only from the
rotary menu on the panel.

It is a thin *bridge*: Sable's real settings live in
`/home/volumio/sable/config/settings.json`, written by the Python app. This plugin
reads that file to render the settings page, and on save it writes the changed keys
back and pings the running app to reload them live -- no service restart, no second
copy of your config.

## What you can change

- **Display** -- now-playing screen (Modern / Spectrum), spectrum style
  (Bars / Dots / Mirror / Ribbon), brightness (Low / Medium / High), and whether
  screens crossfade.
- **Clock** -- show seconds, show date.
- **Screensaver** -- how long until the panel dims (burn-in protection) and how
  long until it switches off.
- **Controls** -- buttons + LEDs on/off, IR remote on/off.
- **Actions** -- reload the panel now, or restart Sable.

Most changes apply instantly. Turning buttons/LEDs or IR on/off changes which
hardware Sable drives, so those take effect after a **Restart Sable** (button in
the Actions section).

## Requirements

- Volumio 3 or newer (Bookworm), on a Raspberry Pi.
- Sable installed and running at `/home/volumio/sable`, as the `sable.service`
  systemd unit (this is what creates the `/tmp/sable-cmd.sock` the plugin talks to).
- Node 16+ (Volumio ships its own).

## Install

SSH into your Volumio box (`ssh volumio@<your-pi>`), then:

### Option A -- Volumio plugin CLI (recommended)

```bash
cd /home/volumio/sable/plugin
volumio plugin install
```

This packages the current folder and installs it. When it finishes, open the web UI:
**Settings -> Plugins -> Installed Plugins**, find **Sable**, and toggle it **ON**.
Then click its gear/cog to open the settings page.

### Option B -- manual copy

```bash
# 1. Copy the plugin into Volumio's plugin tree
sudo mkdir -p /data/plugins/system_hardware/sable
sudo cp -r /home/volumio/sable/plugin/* /data/plugins/system_hardware/sable/

# 2. Install its Node dependencies
cd /data/plugins/system_hardware/sable
npm install --production

# 3. Restart Volumio so it picks up the new plugin
sudo systemctl restart volumio_command
sudo systemctl restart volumio
```

Then enable it under **Settings -> Plugins -> Installed Plugins -> Sable**.

## Use

1. **Settings -> Plugins -> Installed Plugins -> Sable** (gear icon) opens the page.
2. Change values in any section and press that section's **Save**.
3. The panel updates immediately for display/clock/screensaver changes. For
   buttons/LEDs or IR changes, press **Actions -> Restart Sable**.

## Uninstall

Remove it from **Settings -> Plugins -> Installed Plugins** (or
`volumio plugin uninstall` for the `sable` plugin). This only removes the web-UI
editor -- the Sable app and your `settings.json` are left untouched; you simply go
back to editing settings from the panel's rotary menu.

## How it works (for the curious)

- `getUIConfig()` reads `/home/volumio/sable/config/settings.json` and fills the
  form from it.
- Each section's save handler reads that JSON, updates only its keys, writes it
  back atomically, then opens `/tmp/sable-cmd.sock` and sends
  `{"cmd":"reload_config"}`. Sable's IPC server reloads settings and re-renders.
- `settings.json` is the single source of truth; the web UI is just a mirror.
  Changes made on the panel's rotary menu show up here on next page load, and vice
  versa.

## Troubleshooting

- **Page is empty / values look default** -- the plugin could not read
  `settings.json`. Check the file exists and is valid:
  `cat /home/volumio/sable/config/settings.json`.
- **Saves do not change the panel** -- Sable may not be running, so the reload
  ping has nowhere to go. Check `systemctl status sable.service` and that
  `/tmp/sable-cmd.sock` exists. The save still wrote the file; restart Sable to
  apply it.
- **Restart Sable button does nothing** -- it runs
  `sudo systemctl restart sable.service`; that must be allowed without a password
  (it is, by default, on a standard Sable install).
