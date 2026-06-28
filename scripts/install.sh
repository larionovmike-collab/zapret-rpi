#!/bin/bash
set -Eeuo pipefail

ROOT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
CONFIG_FILE=
UPDATE_MODE=0
STATE_DIR=/var/lib/zapret-rpi
BACKUP_DIR=$STATE_DIR/backup/original
MANIFEST=$BACKUP_DIR/manifest
APPLIED=0

usage() { echo "Usage: sudo $0 --config FILE [--update]"; }
die() { echo "ERROR: $*" >&2; exit 1; }

while (($#)); do
    case "$1" in
        --config) CONFIG_FILE=${2:-}; shift 2 ;;
        --update) UPDATE_MODE=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; die "unknown argument: $1" ;;
    esac
done

[[ $EUID -eq 0 ]] || die 'run as root'
[[ -n $CONFIG_FILE && -r $CONFIG_FILE ]] || die 'readable --config is required'
PROJECT_VERSION=$(tr -d '\r\n' <"$ROOT_DIR/VERSION")
ZAPRET2_COMMIT=$(tr -d '\r\n' <"$ROOT_DIR/UPSTREAM_COMMIT")
[[ $PROJECT_VERSION =~ ^[0-9]+\.[0-9]+\.[0-9]+([.-][A-Za-z0-9.-]+)?$ ]] || die 'invalid VERSION'
[[ $ZAPRET2_COMMIT =~ ^[0-9a-f]{40}$ ]] || die 'invalid UPSTREAM_COMMIT'
[[ -f /etc/os-release ]] || die 'unsupported operating system'
. /etc/os-release
[[ ${ID:-} == debian || ${ID_LIKE:-} == *debian* ]] || die 'Debian-compatible OS required'
[[ $(dpkg --print-architecture) == arm64 ]] || die 'arm64 system required by the approved architecture'
[[ -d /sys/class/net/eth0 && -d /sys/class/net/wlan0 ]] || die 'eth0 and wlan0 are required'

SSID=$(sed -n '1p' "$CONFIG_FILE")
WPA_PASSPHRASE=$(sed -n '2p' "$CONFIG_FILE")
COUNTRY=$(sed -n '3p' "$CONFIG_FILE")
CHANNEL=$(sed -n '4p' "$CONFIG_FILE")
[[ $SSID =~ ^[A-Za-z0-9._-]{1,32}$ ]] || die 'SSID must contain 1-32 letters, digits, dot, underscore or dash'
[[ $WPA_PASSPHRASE =~ ^[A-Za-z0-9.!_-]{8,63}$ ]] || die 'WPA passphrase must contain 8-63 letters, digits, dot, underscore, dash or exclamation mark'
[[ $COUNTRY =~ ^[A-Z]{2}$ ]] || die 'country must be a two-letter uppercase code'
case "$CHANNEL" in 1|6|11) ;; *) die 'channel must be 1, 6 or 11' ;; esac

ETHERNET_CIDR=$(ip -4 -o route show dev eth0 scope link | awk '$1 ~ /^[0-9]+\./ && $1 ~ /\// {print $1; exit}')
[[ -n $ETHERNET_CIDR ]] || die 'cannot determine directly connected Ethernet subnet'
ip -4 -o addr show dev eth0 scope global | grep -q . || die 'eth0 has no global IPv4 address'
ip -4 route show default | grep -q 'dev eth0' || die 'default IPv4 route does not use eth0'
systemctl is-active --quiet ssh || die 'SSH service is not active'

if ip -4 route show 10.77.0.0/24 | grep -qv 'dev wlan0'; then
    die '10.77.0.0/24 conflicts with an existing route'
fi
if find /etc/systemd/network /run/systemd/network -maxdepth 1 -type f -name '*.network' \
    ! -name '20-zapret-rpi-wlan0.network' 2>/dev/null | grep -q .; then
    die 'existing local systemd-networkd profiles require manual conflict review'
fi

mkdir -p "$BACKUP_DIR" /etc/zapret-rpi /usr/local/lib/zapret-rpi
chmod 711 "$STATE_DIR"
chmod 700 "$BACKUP_DIR"
touch "$MANIFEST"
chmod 600 "$MANIFEST"

record_original() {
    local target=$1 key
    key=$(printf '%s' "$target" | sed 's#^/##; s#/#__#g')
    awk -F '\t' -v target="$target" '$2 == target { found=1 } END { exit !found }' "$MANIFEST" && return 0
    if [[ -e $target || -L $target ]]; then
        cp -a -- "$target" "$BACKUP_DIR/$key"
        printf 'present\t%s\t%s\n' "$target" "$key" >>"$MANIFEST"
    else
        printf 'absent\t%s\t-\n' "$target" >>"$MANIFEST"
    fi
}

install_managed() {
    local source=$1 target=$2 mode=$3
    record_original "$target"
    install -D -m "$mode" "$source" "$target"
}

record_service() {
    local unit=$1 marker=$BACKUP_DIR/service-$1
    [[ -e $marker ]] && return 0
    {
        systemctl is-enabled "$unit" 2>/dev/null || true
        systemctl is-active "$unit" 2>/dev/null || true
    } >"$marker"
}

rollback_on_error() {
    local rc=$?
    if ((APPLIED)); then
        echo 'Service diagnostics before rollback:' >&2
        systemctl --no-pager --full status zapret2.service zapret-rpi-web.service \
            zapret-rpi-web-lan.service >&2 || true
        journalctl -b -u zapret2.service -u zapret-rpi-web.service \
            -u zapret-rpi-web-lan.service --no-pager -n 100 -o cat >&2 || true
        if ((UPDATE_MODE)); then
            echo 'Update deployment failed; the update wrapper will restore its snapshot.' >&2
        else
            echo 'Deployment failed after changes were applied; rolling back.' >&2
            "$ROOT_DIR/scripts/rollback.sh" || true
        fi
    fi
    exit "$rc"
}
trap rollback_on_error ERR

echo '[1/8] Installing packages'
export DEBIAN_FRONTEND=noninteractive
record_service hostapd.service
record_service dnsmasq.service
record_service systemd-networkd.service
if [[ ! -e $BACKUP_DIR/networkmanager-wlan0-managed ]]; then
    nmcli -g GENERAL.MANAGED device show wlan0 2>/dev/null >"$BACKUP_DIR/networkmanager-wlan0-managed" || echo yes >"$BACKUP_DIR/networkmanager-wlan0-managed"
fi

policy_created=0
if [[ ! -e /usr/sbin/policy-rc.d ]]; then
    printf '#!/bin/sh\nexit 101\n' >/usr/sbin/policy-rc.d
    chmod 755 /usr/sbin/policy-rc.d
    policy_created=1
fi
if ! apt-get update || ! apt-get install -y hostapd dnsmasq nftables iw rfkill iproute2 network-manager curl dnsutils git make gcc pkg-config zlib1g-dev libcap-dev libnetfilter-queue-dev libmnl-dev libluajit-5.1-dev python3 python3-venv sudo; then
    ((policy_created == 0)) || rm -f /usr/sbin/policy-rc.d
    die 'package installation failed'
fi
((policy_created == 0)) || rm -f /usr/sbin/policy-rc.d
systemctl disable --now hostapd.service dnsmasq.service >/dev/null 2>&1 || true

echo '[2/8] Building pinned zapret2 revision'
if [[ ! -d /opt/zapret2/.git ]]; then
    rm -rf /opt/zapret2.new
    git clone https://github.com/bol-van/zapret2.git /opt/zapret2.new
    mv /opt/zapret2.new /opt/zapret2
fi
git -C /opt/zapret2 fetch --depth 1 origin "$ZAPRET2_COMMIT"
git -C /opt/zapret2 checkout --detach "$ZAPRET2_COMMIT"
make -C /opt/zapret2 clean >/dev/null 2>&1 || true
make -C /opt/zapret2
chmod -R a+rX /opt/zapret2
[[ $(git -C /opt/zapret2 rev-parse HEAD) == "$ZAPRET2_COMMIT" ]]
[[ -x /opt/zapret2/nfq2/nfqws2 ]]

echo '[3/8] Rendering and validating configuration'
tmpdir=$(mktemp -d)
trap 'rm -rf "$tmpdir"' EXIT
sed -e "s/@SSID@/$SSID/g" -e "s/@WPA_PASSPHRASE@/$WPA_PASSPHRASE/g" \
    -e "s/@COUNTRY@/$COUNTRY/g" -e "s/@CHANNEL@/$CHANNEL/g" \
    "$ROOT_DIR/configs/hostapd/hostapd.conf.in" >"$tmpdir/hostapd.conf"
sed "s#@ETHERNET_CIDR@#$ETHERNET_CIDR#g" \
    "$ROOT_DIR/configs/nftables/zapret-rpi.nft.in" >"$tmpdir/zapret-rpi.nft"
{
    printf 'PROJECT_VERSION=%s\n' "$PROJECT_VERSION"
    printf 'ZAPRET2_COMMIT=%s\n' "$ZAPRET2_COMMIT"
} >"$tmpdir/release.env"
dnsmasq --test --conf-file="$ROOT_DIR/configs/dnsmasq/zapret-rpi.conf"
"$ROOT_DIR/scripts/apply-nft.sh" --check "$tmpdir/zapret-rpi.nft"

echo '[4/8] Installing managed files'
APPLIED=1
install_managed "$tmpdir/hostapd.conf" /etc/zapret-rpi/hostapd.conf 600
install_managed "$ROOT_DIR/configs/dnsmasq/zapret-rpi.conf" /etc/zapret-rpi/dnsmasq.conf 644
install_managed "$tmpdir/zapret-rpi.nft" /etc/zapret-rpi/zapret-rpi.nft 600
install_managed "$tmpdir/release.env" /etc/zapret-rpi/release.env 644
install_managed "$ROOT_DIR/configs/NetworkManager/10-zapret-rpi-wlan0.conf" /etc/NetworkManager/conf.d/10-zapret-rpi-wlan0.conf 644
install_managed "$ROOT_DIR/configs/networkd/20-zapret-rpi-wlan0.network" /etc/systemd/network/20-zapret-rpi-wlan0.network 644
install_managed "$ROOT_DIR/configs/sysctl.d/90-zapret-rpi-router.conf" /etc/sysctl.d/90-zapret-rpi-router.conf 644
install_managed "$ROOT_DIR/scripts/apply-nft.sh" /usr/local/lib/zapret-rpi/apply-nft.sh 755
for unit in zapret-rpi-nftables zapret-rpi-hostapd zapret-rpi-dnsmasq; do
    install_managed "$ROOT_DIR/systemd/$unit.service" "/etc/systemd/system/$unit.service" 644
done
install_managed "$ROOT_DIR/systemd/zapret2.service" /etc/systemd/system/zapret2.service 644
install_managed "$ROOT_DIR/configs/zapret2/config" /opt/zapret2/config 600
mkdir -p /opt/zapret2/blockcheck2.d/zapret-rpi-quick
for test_script in "$ROOT_DIR"/configs/zapret2/blockcheck2.d/zapret-rpi-quick/*.sh; do
    install_managed "$test_script" "/opt/zapret2/blockcheck2.d/zapret-rpi-quick/$(basename "$test_script")" 644
done
mkdir -p /etc/zapret-rpi/zapret2/profiles
for profile in "$ROOT_DIR"/configs/zapret2/profiles/*.conf; do
    install_managed "$profile" "/etc/zapret-rpi/zapret2/profiles/$(basename "$profile")" 600
done
record_original /etc/zapret-rpi/zapret2/active.conf
active_profile=$(readlink -f /etc/zapret-rpi/zapret2/active.conf 2>/dev/null || true)
if [[ $active_profile != /etc/zapret-rpi/zapret2/profiles/*.conf || ! -f $active_profile ]]; then
    ln -sfn /etc/zapret-rpi/zapret2/profiles/standard.conf /etc/zapret-rpi/zapret2/active.conf
fi
mkdir -p /opt/zapret2/init.d/sysv/custom.d
install_managed /opt/zapret2/init.d/custom.d.examples.linux/99-lan-filter /opt/zapret2/init.d/sysv/custom.d/99-lan-filter 755
rm -f /opt/zapret2/init.d/custom.d/99-lan-filter
install_managed "$ROOT_DIR/scripts/profile.sh" /usr/local/sbin/zapret-rpi-profile 755
install_managed "$ROOT_DIR/scripts/validate.sh" /usr/local/sbin/zapret-rpi-validate 755
install_managed "$ROOT_DIR/scripts/smoke-test.sh" /usr/local/sbin/zapret-rpi-smoke-test 755
install_managed "$ROOT_DIR/scripts/rollback.sh" /usr/local/sbin/zapret-rpi-rollback 755
install_managed "$ROOT_DIR/scripts/autotune.py" /usr/local/sbin/zapret-rpi-autotune 755
install_managed "$ROOT_DIR/systemd/zapret-rpi-autotune.service" /etc/systemd/system/zapret-rpi-autotune.service 644
install_managed "$ROOT_DIR/systemd/zapret-rpi-autocheck.service" /etc/systemd/system/zapret-rpi-autocheck.service 644
install_managed "$ROOT_DIR/systemd/zapret-rpi-autocheck.timer" /etc/systemd/system/zapret-rpi-autocheck.timer 644
id zapret-web >/dev/null 2>&1 || useradd --system --home /nonexistent --shell /usr/sbin/nologin zapret-web
mkdir -p /usr/local/lib/zapret-rpi/web /var/lib/zapret-rpi/web /var/lib/zapret-rpi/autotune/jobs
chown zapret-web:zapret-web /var/lib/zapret-rpi/web
chown root:root /var/lib/zapret-rpi/autotune /var/lib/zapret-rpi/autotune/jobs
chmod 700 /var/lib/zapret-rpi/web /var/lib/zapret-rpi/autotune /var/lib/zapret-rpi/autotune/jobs
install_managed "$ROOT_DIR/scripts/web-helper.py" /usr/local/sbin/zapret-rpi-web-helper 755
install_managed "$ROOT_DIR/configs/sudoers/zapret-rpi-web" /etc/sudoers.d/zapret-rpi-web 440
visudo -cf /etc/sudoers.d/zapret-rpi-web >/dev/null
install_managed "$ROOT_DIR/systemd/zapret-rpi-web.service" /etc/systemd/system/zapret-rpi-web.service 644
install_managed "$ROOT_DIR/systemd/zapret-rpi-web-lan.service" /etc/systemd/system/zapret-rpi-web-lan.service 644
rm -rf /usr/local/lib/zapret-rpi/web/zapret_ui /usr/local/lib/zapret-rpi/web/static
cp -a "$ROOT_DIR/web/backend/zapret_ui" /usr/local/lib/zapret-rpi/web/zapret_ui
cp -a "$ROOT_DIR/web/frontend/dist" /usr/local/lib/zapret-rpi/web/static
rm -rf /usr/local/lib/zapret-rpi/web/venv
python3 -m venv /usr/local/lib/zapret-rpi/web/venv
/usr/local/lib/zapret-rpi/web/venv/bin/pip install --disable-pip-version-check -r "$ROOT_DIR/web/backend/requirements.txt"
# The public bootstrap deliberately uses umask 077.  Make the root-owned web
# runtime traversable and readable by its unprivileged service account.
chown -R root:root /usr/local/lib/zapret-rpi/web
chmod 755 /usr/local/lib/zapret-rpi /usr/local/lib/zapret-rpi/web
chmod -R u=rwX,go=rX /usr/local/lib/zapret-rpi/web
runuser -u zapret-web -- test -x /usr/local/lib/zapret-rpi/web
runuser -u zapret-web -- test -x /usr/local/lib/zapret-rpi/web/venv/bin/uvicorn
runuser -u zapret-web -- test -r /usr/local/lib/zapret-rpi/web/zapret_ui/main.py
echo '[5/8] Assigning wlan0 ownership without changing eth0'
rfkill unblock wifi || true
nmcli general reload
nmcli device set wlan0 managed no || true
systemctl enable --now systemd-networkd.service
networkctl reload
networkctl reconfigure wlan0 || true
ip link set wlan0 up
ip address replace 10.77.0.1/24 dev wlan0

echo '[6/8] Enabling forwarding and project services'
sysctl --system >/dev/null
systemctl daemon-reload
systemctl enable zapret-rpi-nftables.service zapret-rpi-hostapd.service zapret-rpi-dnsmasq.service zapret2.service zapret-rpi-web.service zapret-rpi-web-lan.service zapret-rpi-autocheck.timer
systemctl restart zapret-rpi-nftables.service
systemctl restart zapret-rpi-hostapd.service
systemctl restart zapret-rpi-dnsmasq.service
systemctl restart zapret2.service
systemctl restart zapret-rpi-web.service
systemctl restart zapret-rpi-web-lan.service
systemctl restart zapret-rpi-autocheck.timer

echo '[7/8] Verifying Ethernet SSH invariants'
ip -4 -o addr show dev eth0 scope global | grep -q .
ip -4 route show default | grep -q 'dev eth0'
ss -lnt | grep -q ':22[[:space:]]'

echo '[8/8] Running health checks'
/usr/local/sbin/zapret-rpi-validate
trap - ERR
rm -f "$CONFIG_FILE"
echo 'Deployment completed. zapret2 is active for forwarded Wi-Fi clients.'
