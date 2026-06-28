#!/bin/sh
set -eu

CHECK_ONLY=0
if [ "${1:-}" = --check ]; then CHECK_ONLY=1; shift; fi
CONFIG=${1:-/etc/zapret-rpi/zapret-rpi.nft}
CANDIDATE=$(mktemp)
trap 'rm -f "$CANDIDATE"' EXIT

if /usr/sbin/nft list table inet zapret_rpi >/dev/null 2>&1; then
    printf 'delete table inet zapret_rpi\n' >"$CANDIDATE"
fi
sed -n 'p' "$CONFIG" >>"$CANDIDATE"
/usr/sbin/nft -c -f "$CANDIDATE"
[ "$CHECK_ONLY" -eq 1 ] || /usr/sbin/nft -f "$CANDIDATE"
