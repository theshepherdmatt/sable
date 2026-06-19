#!/usr/bin/env bash
# Vendor the Volumio ir_controller remote-profile LIBRARY into the repo.
#
# This is a DEV/git fetch tool -- it is NOT the Pi installer and never touches
# /etc/lirc or any running service. It pulls ONLY each remote's lircd.conf (the
# key->code map) from the upstream ir_controller plugin into
#   config/lirc/profiles/<RemoteName>/lircd.conf
# and deliberately DROPS the upstream lircrc files: Sable's in-process listener
# (src/sable/inputs/ir.py) is the ONLY action layer, so there is no irexec/lircrc
# to ship. Re-run any time to refresh the library against upstream.
#
# Requires: bash, curl. (No jq/python needed -- the GitHub tree JSON is grepped.)
set -euo pipefail

REPO="volumio/volumio-plugins-sources-bookworm"
BRANCH="master"
SRC="ir_controller/configurations"
API="https://api.github.com/repos/$REPO/git/trees/$BRANCH?recursive=1"
RAW="https://raw.githubusercontent.com/$REPO/$BRANCH"

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DST="$ROOT/config/lirc/profiles"

echo "Fetching ir_controller profile list from $REPO@$BRANCH ..."
TREE="$(curl -fsSL "$API")"

# Profile names = every configurations/<Name>/lircd.conf in the tree. Names may
# contain spaces, so read NUL-free line by line (no name has a newline).
NAMES="$(printf '%s' "$TREE" \
  | grep -oE "\"$SRC/[^/\"]+/lircd.conf\"" \
  | sed -E "s#\"$SRC/##; s#/lircd.conf\"##" \
  | sort -u)"

if [ -z "$NAMES" ]; then
  echo "FATAL: no profiles found in tree (upstream layout changed?)" >&2
  exit 1
fi

count=0
while IFS= read -r name; do
  [ -n "$name" ] || continue
  enc="${name// /%20}"                     # URL-encode spaces (only special char)
  out="$DST/$name"
  mkdir -p "$out"
  if curl -fsSL "$RAW/$SRC/$enc/lircd.conf" -o "$out/lircd.conf"; then
    count=$((count + 1))
    echo "  + $name"
  else
    echo "  ! FAILED: $name" >&2
  fi
done <<EOF
$NAMES
EOF

# Keep Sable's OWN "ApEvo" profile (Audiophonics EVO Sabre remote) in the library.
# It is NOT in upstream ir_controller, so seed it from the canonical copy that
# install.sh has always shipped (config/lirc/lircd.conf) -- kept byte-identical.
APEVO_SRC="$ROOT/config/lirc/lircd.conf"
if [ -f "$APEVO_SRC" ]; then
  mkdir -p "$DST/ApEvo"
  cp -a "$APEVO_SRC" "$DST/ApEvo/lircd.conf"
  count=$((count + 1))
  echo "  + ApEvo (Sable's own; copied from config/lirc/lircd.conf)"
fi

# Preserve upstream attribution (the repo ships no LICENSE file; the plugin's
# README is the only attribution artifact).
curl -fsSL "$RAW/ir_controller/README.md" -o "$DST/UPSTREAM-README.md" 2>/dev/null \
  && echo "  + UPSTREAM-README.md (attribution)" || true

cat > "$DST/ATTRIBUTION.md" <<ATTR
# IR remote profiles -- attribution

The remote profiles in this directory (each \`<RemoteName>/lircd.conf\`) are
vendored from the Volumio **ir_controller** plugin:

  https://github.com/$REPO
  path: $SRC/

Only the \`lircd.conf\` (the IR key -> code map) is taken from each profile. The
upstream \`lircrc\` files are intentionally NOT vendored: Sable maps keys to
actions in-process (src/sable/inputs/ir.py), so it ships no irexec/lircrc.

\`ApEvo/\` is Sable's OWN profile (Audiophonics EVO Sabre remote), not from
upstream; it is seeded from config/lirc/lircd.conf.

Refresh with: tools/update-ir-profiles.sh
ATTR
echo "  + ATTRIBUTION.md"

echo
echo "Done. $count profiles in $DST"
