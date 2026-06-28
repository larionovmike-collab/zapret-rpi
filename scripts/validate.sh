#!/bin/sh
set -eu

fail=0
check() {
    name=$1
    shift
    if "$@"; then printf '[OK] %s\n' "$name"; else printf '[FAIL] %s\n' "$name" >&2; fail=1; fi
}

check 'Ethernet interface exists' test -d /sys/class/net/eth0
check 'Wi-Fi interface exists' test -d /sys/class/net/wlan0
check 'Ethernet has IPv4 address' sh -c "ip -4 -o addr show dev eth0 scope global | grep -q ."
check 'Default route uses Ethernet' sh -c "ip -4 route show default | grep -q 'dev eth0'"
check 'SSH daemon is active' systemctl is-active --quiet ssh
check 'wlan0 address is configured' sh -c "ip -4 -o addr show dev wlan0 | grep -q '10.77.0.1/24'"
check 'IPv4 forwarding is enabled' sh -c "test \"$(sysctl -n net.ipv4.ip_forward)\" = 1"
check 'dnsmasq configuration' dnsmasq --test --conf-file=/etc/zapret-rpi/dnsmasq.conf
check 'nftables configuration' /usr/local/lib/zapret-rpi/apply-nft.sh --check
check 'project nftables table is loaded' nft list table inet zapret_rpi
check 'hostapd service is active' systemctl is-active --quiet zapret-rpi-hostapd.service
check 'dnsmasq service is active' systemctl is-active --quiet zapret-rpi-dnsmasq.service
check 'nftables service is active' systemctl is-active --quiet zapret-rpi-nftables.service
check 'SSH listens on TCP 22' sh -c "ss -lnt | grep -q ':22[[:space:]]'"
check 'web UI service is active' systemctl is-active --quiet zapret-rpi-web.service
check 'LAN HTTP web UI service is active' systemctl is-active --quiet zapret-rpi-web-lan.service
check 'web UI listens on Wi-Fi address' sh -c '
for _ in $(seq 1 20); do
    if ss -H -ltn | awk "{print \$4}" | grep -Eq "(^|[^0-9])10[.]77[.]0[.]1(%[^:]*)?:8080$"; then
        exit 0
    fi
    sleep 0.5
done
ss -ltn >&2
exit 1
'
check 'LAN HTTP web UI listens on TCP 80' sh -c '
for _ in $(seq 1 20); do
    if ss -H -ltn | awk "{print \$4}" | grep -Eq "(^|:|\\])80$"; then
        exit 0
    fi
    sleep 0.5
done
ss -ltn >&2
exit 1
'
check 'autotune runner is installed' test -x /usr/local/sbin/zapret-rpi-autotune
check 'autotune unit is installed' test -f /etc/systemd/system/zapret-rpi-autotune.service
check 'blockcheck2 test set is available' test -d /opt/zapret2/blockcheck2.d/standard
check 'bounded web autotune test set is available' test -f /opt/zapret2/blockcheck2.d/zapret-rpi-quick/10-curated.sh
check 'blockcheck2 DNS lookup prerequisite is installed' sh -c 'command -v nslookup >/dev/null || command -v host >/dev/null'
check 'autotune state is root-only' sh -c "test \"$(stat -c %a /var/lib/zapret-rpi/autotune)\" = 700"
check 'web runtime is accessible by service user' runuser -u zapret-web -- test -x /usr/local/lib/zapret-rpi/web
check 'web executable is accessible by service user' runuser -u zapret-web -- test -x /usr/local/lib/zapret-rpi/web/venv/bin/uvicorn
check 'web application is readable by service user' runuser -u zapret-web -- test -r /usr/local/lib/zapret-rpi/web/zapret_ui/main.py
check 'zapret2 Lua runtime is readable by daemon user' runuser -u tpws -- test -r /opt/zapret2/lua/zapret-lib.lua

check 'zapret2 service is active' systemctl is-active --quiet zapret2.service
check 'nfqws2 process is active' pgrep -x nfqws2
check 'release metadata is installed' test -r /etc/zapret-rpi/release.env
check 'zapret2 revision is pinned' sh -c '. /etc/zapret-rpi/release.env && test -n "$ZAPRET2_COMMIT" && test "$(git -C /opt/zapret2 rev-parse HEAD)" = "$ZAPRET2_COMMIT"'
check 'active strategy profile is valid' /usr/local/sbin/zapret-rpi-profile get
check 'zapret2 nftables table is loaded' nft list table inet zapret2
check 'Wi-Fi forward mark hook is loaded' nft list chain inet zapret2 forward_lan_filter
check 'local output is not marked for zapret2' sh -c "! nft list table inet zapret2 | grep -q 'chain output_lan_filter'"
check 'incoming NFQUEUE hooks are disabled' sh -c "! nft list table inet zapret2 | grep -Eq 'ct reply packets.*queue'"

exit "$fail"
