'use strict';

// Sable plugin uninstall. Stops + disables the Sable services so nothing starts
// on boot. The app dir (/home/volumio/sable) and its settings.json/hardware.json
// are LEFT in place, so reinstalling keeps your configuration -- delete that
// folder by hand for a clean slate. The MPD fifo output is harmless if left.
var exec = require('child_process').exec;

var cmds = [
  '/usr/bin/sudo /bin/systemctl stop sable.service',
  '/usr/bin/sudo /bin/systemctl disable sable.service',
  '/usr/bin/sudo /bin/systemctl stop sable-lirc-post.service',
  '/usr/bin/sudo /bin/systemctl disable sable-lirc-post.service',
  '/usr/bin/sudo /bin/systemctl daemon-reload'
].join(' ; ');

exec(cmds, function () {
  // Always succeed -- a missing/already-stopped unit must not block uninstall.
  process.exit(0);
});
