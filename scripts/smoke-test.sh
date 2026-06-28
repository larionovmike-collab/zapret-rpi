#!/bin/sh
set -eu

echo '== interfaces =='
ip -br address show eth0
ip -br address show wlan0
echo '== routes =='
ip -4 route
echo '== services =='
systemctl --no-pager --full status zapret-rpi-nftables.service zapret-rpi-hostapd.service zapret-rpi-dnsmasq.service
echo '== nftables =='
nft list table inet zapret_rpi
echo '== DHCP leases =='
test ! -e /var/lib/misc/dnsmasq.leases || cat /var/lib/misc/dnsmasq.leases
echo '== forwarding =='
sysctl net.ipv4.ip_forward net.ipv6.conf.all.forwarding
echo '== zapret2 =='
systemctl --no-pager --full status zapret2.service
/usr/local/sbin/zapret-rpi-profile get
nft list table inet zapret2
pgrep -af nfqws2
