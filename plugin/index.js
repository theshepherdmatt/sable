'use strict';

// Sable settings bridge for Volumio.
//
// Sable's source of truth is /home/volumio/sable/config/settings.json (written
// atomically by the Python app). This plugin renders the Volumio settings page
// FROM that file and, on save, writes the changed keys BACK to it and pings the
// running app over its IPC socket to reload -- no service restart, no second
// config store. See sable/settings.py (DEFAULTS) and sable/ipc.py (the socket).

var fs = require('fs-extra');
var path = require('path');
var net = require('net');
var libQ = require('kew');

var SETTINGS_PATH = '/home/volumio/sable/config/settings.json';
var SOCK = '/tmp/sable-cmd.sock';

module.exports = SablePlugin;

function SablePlugin(context) {
  this.context = context;
  this.commandRouter = context.coreCommand;
  this.logger = context.logger;
  this.configManager = context.configManager;
}

// --- lifecycle ---------------------------------------------------------------

SablePlugin.prototype.onVolumioStart = function () {
  var configFile = this.commandRouter.pluginManager.getConfigurationFile(
    this.context, 'config.json');
  this.config = new (require('v-conf'))();
  this.config.loadFile(configFile);
  return libQ.resolve();
};

SablePlugin.prototype.onStart = function () {
  var self = this;
  var defer = libQ.defer();
  require('child_process').exec(
    '/usr/bin/sudo /bin/systemctl start sable.service', function (err) {
      if (err) { self.logger.error('Sable: onStart (start service) failed: ' + err.message); }
      else { self.logger.info('Sable: service started'); }
      defer.resolve();   // resolve regardless so the plugin still loads its UI
    });
  return defer.promise;
};

SablePlugin.prototype.onStop = function () {
  var self = this;
  var defer = libQ.defer();
  require('child_process').exec(
    '/usr/bin/sudo /bin/systemctl stop sable.service', function (err) {
      if (err) { self.logger.error('Sable: onStop (stop service) failed: ' + err.message); }
      defer.resolve();
    });
  return defer.promise;
};

SablePlugin.prototype.onRestart = function () { return this.restartSable(); };
SablePlugin.prototype.getConfigurationFiles = function () { return ['config.json']; };

// --- settings.json helpers ---------------------------------------------------

SablePlugin.prototype._read = function () {
  try { return fs.readJsonSync(SETTINGS_PATH); }
  catch (e) { this.logger.info('Sable: settings.json unreadable: ' + e.message); return {}; }
};

SablePlugin.prototype._write = function (s) {
  fs.ensureDirSync(path.dirname(SETTINGS_PATH));
  var tmp = SETTINGS_PATH + '.uitmp';
  fs.writeJsonSync(tmp, s, { spaces: 2 });
  fs.moveSync(tmp, SETTINGS_PATH, { overwrite: true });   // atomic-ish replace
};

SablePlugin.prototype._get = function (s, section, key, dflt) {
  return (s[section] && s[section][key] !== undefined) ? s[section][key] : dflt;
};

SablePlugin.prototype._set = function (s, section, key, value) {
  if (!s[section]) { s[section] = {}; }
  s[section][key] = value;
};

// Volumio passes select values as { value, label }; switches as bool; number
// inputs as numeric strings. Normalise to the stored primitive.
SablePlugin.prototype._val = function (v) {
  if (v && typeof v === 'object' && v.value !== undefined) { return v.value; }
  return v;
};

SablePlugin.prototype._int = function (v, dflt) {
  var n = parseInt(this._val(v), 10);
  return isNaN(n) ? dflt : n;
};

// Ping the running app to reload settings (fire-and-forget; OK if app is down).
SablePlugin.prototype._reload = function () {
  var self = this;
  try {
    var c = net.connect(SOCK, function () {
      c.write(JSON.stringify({ cmd: 'reload_config' }) + '\n');
      c.end();
    });
    c.setTimeout(1500, function () { c.destroy(); });
    c.on('error', function (e) {
      self.logger.info('Sable: reload ping failed (app not running?): ' + e.message);
    });
  } catch (e) { /* ignore */ }
};

// --- UI ----------------------------------------------------------------------

// Find a content element by id across all sections (index-independent).
SablePlugin.prototype._el = function (uiconf, id) {
  for (var i = 0; i < uiconf.sections.length; i++) {
    var content = uiconf.sections[i].content || [];
    for (var j = 0; j < content.length; j++) {
      if (content[j].id === id) { return content[j]; }
    }
  }
  return null;
};

SablePlugin.prototype._fillSwitch = function (uiconf, id, value) {
  var el = this._el(uiconf, id);
  if (el) { el.value = !!value; }
};

SablePlugin.prototype._fillInput = function (uiconf, id, value) {
  var el = this._el(uiconf, id);
  if (el) { el.value = value; }
};

// For a select, value must be the { value, label } of the matching option.
SablePlugin.prototype._fillSelect = function (uiconf, id, value) {
  var el = this._el(uiconf, id);
  if (!el) { return; }
  var opts = el.options || [];
  for (var i = 0; i < opts.length; i++) {
    if (opts[i].value === value) { el.value = { value: opts[i].value, label: opts[i].label }; return; }
  }
  el.value = { value: value, label: String(value) };   // unknown -> show raw
};

SablePlugin.prototype.getUIConfig = function () {
  var self = this;
  var defer = libQ.defer();
  var lang = __dirname + '/i18n/strings_en.json';
  var ui = __dirname + '/UIConfig.json';
  self.commandRouter.i18nJson(lang, lang, ui)
    .then(function (uiconf) {
      var s = self._read();
      self._fillSelect(uiconf, 'display_screen', self._get(s, 'display', 'screen', 'modern'));
      self._fillSelect(uiconf, 'theme', self._get(s, 'display', 'theme', 'panel'));
      self._fillSelect(uiconf, 'spectrum_style', self._get(s, 'display', 'spectrum_style', 'bars'));
      self._fillSelect(uiconf, 'brightness', self._get(s, 'display', 'brightness', 'high'));
      self._fillSwitch(uiconf, 'transitions', self._get(s, 'display', 'transitions', true));
      self._fillSelect(uiconf, 'display_rotate', self._int(self._get(s, 'display', 'rotate', 0), 0));
      self._fillSwitch(uiconf, 'show_seconds', self._get(s, 'clock', 'show_seconds', false));
      self._fillSwitch(uiconf, 'show_date', self._get(s, 'clock', 'show_date', false));
      self._fillInput(uiconf, 'clock_after_s', self._get(s, 'screensaver', 'clock_after_s', 300));
      self._fillInput(uiconf, 'dim_s', self._get(s, 'screensaver', 'dim_s', 120));
      self._fillInput(uiconf, 'idle_s', self._get(s, 'screensaver', 'idle_s', 3600));
      self._fillSwitch(uiconf, 'leds_enabled', self._get(s, 'controls', 'leds_enabled', true));
      self._fillSwitch(uiconf, 'ir_enabled', self._get(s, 'ir', 'enabled', true));
      defer.resolve(uiconf);
    })
    .fail(function (e) {
      self.logger.error('Sable: getUIConfig failed: ' + e);
      defer.reject(new Error());
    });
  return defer.promise;
};

SablePlugin.prototype.setUIConfig = function () { return libQ.resolve(); };
SablePlugin.prototype.getConf = function () { return libQ.resolve(); };
SablePlugin.prototype.setConf = function () { return libQ.resolve(); };

// --- save handlers (one per section) -----------------------------------------

SablePlugin.prototype._saved = function (msg) {
  this.commandRouter.pushToastMessage('success', 'Sable', msg || 'Settings saved');
};

SablePlugin.prototype.saveDisplay = function (data) {
  var s = this._read();
  var oldRotate = this._int(this._get(s, 'display', 'rotate', 0), 0);
  var newRotate = this._int(data.display_rotate, 0);
  this._set(s, 'display', 'screen', this._val(data.display_screen));
  this._set(s, 'display', 'theme', this._val(data.theme));
  this._set(s, 'display', 'spectrum_style', this._val(data.spectrum_style));
  this._set(s, 'display', 'brightness', this._val(data.brightness));
  this._set(s, 'display', 'transitions', !!data.transitions);
  this._set(s, 'display', 'rotate', newRotate);
  this._write(s);
  if (newRotate !== oldRotate) {
    // Rotation is baked into the luma device at construction, so a live reload
    // can't apply it -- the service must re-init. Restart instead of ping.
    this.restartSable();
    this._saved('Display saved -- restarting Sable to apply screen rotation');
  } else {
    this._reload();
    this._saved('Display settings saved');
  }
  return libQ.resolve();
};

SablePlugin.prototype.saveClock = function (data) {
  var s = this._read();
  this._set(s, 'clock', 'show_seconds', !!data.show_seconds);
  this._set(s, 'clock', 'show_date', !!data.show_date);
  this._write(s);
  this._reload();
  this._saved('Clock settings saved');
  return libQ.resolve();
};

SablePlugin.prototype.saveScreensaver = function (data) {
  var s = this._read();
  this._set(s, 'screensaver', 'clock_after_s', this._int(data.clock_after_s, 300));
  this._set(s, 'screensaver', 'dim_s', this._int(data.dim_s, 120));
  this._set(s, 'screensaver', 'idle_s', this._int(data.idle_s, 3600));
  this._write(s);
  this._reload();
  this._saved('Screensaver settings saved');
  return libQ.resolve();
};

SablePlugin.prototype.saveControls = function (data) {
  var s = this._read();
  this._set(s, 'controls', 'leds_enabled', !!data.leds_enabled);
  this._set(s, 'ir', 'enabled', !!data.ir_enabled);
  this._write(s);
  this._reload();
  this._saved('Control settings saved (restart Sable to apply hardware changes)');
  return libQ.resolve();
};

// --- actions -----------------------------------------------------------------

SablePlugin.prototype.reloadSable = function () {
  this._reload();
  this._saved('Asked the panel to reload settings');
  return libQ.resolve();
};

SablePlugin.prototype.restartSable = function () {
  var self = this;
  var exec = require('child_process').exec;
  exec('/usr/bin/sudo /bin/systemctl restart sable.service', function (err) {
    if (err) {
      self.commandRouter.pushToastMessage('error', 'Sable', 'Restart failed: ' + err.message);
    } else {
      self.commandRouter.pushToastMessage('success', 'Sable', 'Sable restarted');
    }
  });
  return libQ.resolve();
};
