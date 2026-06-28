#!/bin/bash
set -euo pipefail

BACKUP_DIR=/var/lib/zapret-rpi/backup/original
MANIFEST=$BACKUP_DIR/manifest
[[ $EUID -eq 0 ]] || { echo 'Run as root.' >&2; exit 1; }
[[ -r $MANIFEST ]] || { echo 'No zapret-rpi backup manifest found.' >&2; exit 1; }

systemctl stop zapret-rpi-autotune.service >/dev/null 2>&1 || true
systemctl disable --now zapret-rpi-web-lan.service zapret-rpi-web.service zapret2.service zapret-rpi-hostapd.service zapret-rpi-dnsmasq.service zapret-rpi-nftables.service >/dev/null 2>&1 || true
nft list table inet zapret2 >/dev/null 2>&1 && nft delete table inet zapret2 || true
nft list table inet zapret_rpi >/dev/null 2>&1 && nft delete table inet zapret_rpi || true
rm -rf /usr/local/lib/zapret-rpi/web /var/lib/zapret-rpi/web /var/lib/zapret-rpi/autotune
rm -f /etc/zapret-rpi/zapret2/profiles/auto-*.conf /etc/zapret-rpi/zapret2/profiles/autotune.conf

while IFS=$'\t' read -r state target key; do
    [[ -n $target ]] || continue
    if [[ $state == present ]]; then
        rm -f -- "$target"
        cp -a -- "$BACKUP_DIR/$key" "$target"
    else
        rm -f -- "$target"
    fi
done <"$MANIFEST"

restore_service() {
    local unit=$1 marker=$BACKUP_DIR/service-$1 enabled active
    [[ -r $marker ]] || return 0
    enabled=$(sed -n '1p' "$marker")
    active=$(sed -n '2p' "$marker")
    [[ $enabled == enabled ]] && systemctl enable "$unit" >/dev/null 2>&1 || systemctl disable "$unit" >/dev/null 2>&1 || true
    [[ $active == active ]] && systemctl start "$unit" || systemctl stop "$unit" >/dev/null 2>&1 || true
}

systemctl daemon-reload
restore_service systemd-networkd.service
restore_service hostapd.service
restore_service dnsmasq.service
nmcli general reload >/dev/null 2>&1 || true
managed=$(sed -n '1p' "$BACKUP_DIR/networkmanager-wlan0-managed" 2>/dev/null || echo yes)
nmcli device set wlan0 managed "$managed" >/dev/null 2>&1 || true
ip address del 10.77.0.1/24 dev wlan0 >/dev/null 2>&1 || true
sysctl --system >/dev/null || true
echo 'zapret-rpi changes rolled back; installed Debian packages were retained.'
