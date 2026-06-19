# IR remote profiles -- attribution

The remote profiles in this directory (each `<RemoteName>/lircd.conf`) are
vendored from the Volumio **ir_controller** plugin:

  https://github.com/volumio/volumio-plugins-sources-bookworm
  path: ir_controller/configurations/

Only the `lircd.conf` (the IR key -> code map) is taken from each profile. The
upstream `lircrc` files are intentionally NOT vendored: Sable maps keys to
actions in-process (src/sable/inputs/ir.py), so it ships no irexec/lircrc.

`ApEvo/` is Sable's OWN profile (Audiophonics EVO Sabre remote), not from
upstream; it is seeded from config/lirc/lircd.conf.

Refresh with: tools/update-ir-profiles.sh
