'use strict';

// Sable is not uninstalled by removing this plugin -- the plugin is only a UI
// bridge to /home/volumio/sable/config/settings.json. Removing it leaves the
// running app and its settings untouched; you simply lose the web-UI editor.
var exec = require('child_process').exec;
exec('/bin/echo done', function () { process.exit(0); });
