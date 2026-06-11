#!/usr/bin/env bash
# Sable LIRC post-setup. Runs once at install and on every boot (via
# sable-lirc-post.service, ordered After lircd):
#   - neutralise irexec so it never runs anything on a key press,
#   - make the lircd broadcast socket world-readable so Sable (user 'volumio')
#     can read it.
# Idempotent and best-effort.
set -uo pipefail
pkill -x irexec 2>/dev/null || true
echo "# Sable: irexec intentionally empty" > /etc/lirc/irexec.lircrc 2>/dev/null || true
[ -S /run/lirc/lircd ] && chmod 666 /run/lirc/lircd 2>/dev/null || true
true
