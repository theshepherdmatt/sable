'use strict';

// Sable Volumio plugin controller.
//
// Scope: plugin lifecycle + the settings UI ONLY. The display/controls/IR run as
// independent systemd services (sable-display, sable-controls, sable-ir); this
// controller never draws, reads buttons, or owns the FSM.
//
// Config model: ONE source of truth = config/settings.json (written by the
// Python app and by setUIConfig here). The Volumio UI is a read-only mirror. On
// save we write settings.json atomically and ping the display over the IPC
// socket to reload -- no blind "systemctl restart".
//
// ASCII-ONLY: tools/ascii_guard.py runs in CI over this file. No smart quotes,
// em-dashes, or other non-ASCII bytes, ever (the old code crashed on exactly
// that). There is no MCP-address field in the UI -- the address is a fixed
// hardware constant in hardware.py, so the old coerceHexAddr mess is gone.

const libQ = require('kew');
const fs = require('fs');
const net = require('net');
const path = require('path');

const SETTINGS_PATH = path.join(__dirname, '..', 'config', 'settings.json');
const IPC_SOCKET = process.env.SABLE_SOCK || '/tmp/sable-cmd.sock';

module.exports = ControllerSable;

function ControllerSable(context) {
  this.context = context;
  this.commandRouter = context.coreCommand;
  this.logger = context.logger;
}

ControllerSable.prototype.onVolumioStart = function () {
  return libQ.resolve();
};

ControllerSable.prototype.onStart = function () {
  // The display/controls/IR services run on their own; nothing to start here.
  return libQ.resolve();
};

ControllerSable.prototype.onStop = function () {
  return libQ.resolve();
};

// --- settings (read-only mirror of settings.json) ---

ControllerSable.prototype.readSettings = function () {
  try {
    return JSON.parse(fs.readFileSync(SETTINGS_PATH, 'utf8'));
  } catch (e) {
    return {};
  }
};

ControllerSable.prototype.writeSettingsAtomic = function (data) {
  const dir = path.dirname(SETTINGS_PATH);
  const tmp = path.join(dir, '.settings.' + process.pid + '.tmp');
  fs.writeFileSync(tmp, JSON.stringify(data, null, 2));
  fs.renameSync(tmp, SETTINGS_PATH);
};

ControllerSable.prototype.pingReload = function () {
  try {
    const c = net.createConnection(IPC_SOCKET, function () {
      c.end(JSON.stringify({ cmd: 'reload_config', arg: null }) + '\n');
    });
    c.on('error', function () {});
  } catch (e) {
    // display app may be down; settings.json is still the source of truth.
  }
};

ControllerSable.prototype.getUIConfig = function () {
  // Phase 0 stub: render UIConfig.json from settings.json here next pass.
  return libQ.resolve({});
};

ControllerSable.prototype.setUIConfig = function (data) {
  const settings = this.readSettings();
  // merge incoming UI values into settings (validated next pass), then:
  this.writeSettingsAtomic(Object.assign(settings, data || {}));
  this.pingReload();
  return libQ.resolve();
};
