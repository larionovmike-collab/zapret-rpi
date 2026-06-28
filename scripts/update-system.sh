#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

SOURCE_DIR=
INSTALL_ROOT=/opt/zapret-rpi
BACKUP_ROOT=/var/backups/zapret-rpi-updates
BACKUP_DIR=
ARCHIVE=
CONFIG_FILE=

usage() { echo "Usage: sudo $0 --source DIR"; }
die() { echo "ERROR: $*" >&2; exit 1; }

while (($#)); do
    case "$1" in
        --source) SOURCE_DIR=${2:-}; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; die "unknown argument: $1" ;;
    esac
done

[[ $EUID -eq 0 ]] || die 'run as root'
[[ -n $SOURCE_DIR && -d $SOURCE_DIR ]] || die 'readable --source directory is required'
SOURCE_DIR=$(cd "$SOURCE_DIR" && pwd)
for required in VERSION UPSTREAM_COMMIT scripts/install.sh web/frontend/dist/index.html; do
    [[ -e $SOURCE_DIR/$required ]] || die "update source is missing $required"
done
[[ -r /var/lib/zapret-rpi/backup/original/manifest ]] || die 'original installation backup is missing'
if [[ -d $INSTALL_ROOT && -r $INSTALL_ROOT/VERSION && -r $INSTALL_ROOT/UPSTREAM_COMMIT ]]; then
    LEGACY_INSTALL=0
elif [[ -x /usr/local/sbin/zapret-rpi-validate && -d /opt/zapret2/.git ]]; then
    LEGACY_INSTALL=1
else
    die 'existing zapret-rpi installation is incomplete'
fi
if systemctl is-active --quiet zapret-rpi-autotune.service; then
    die 'autotune is running; wait for it to finish or cancel it before updating'
fi

read_config() {
    local key=$1
    sed -n "s/^${key}=//p" /etc/zapret-rpi/hostapd.conf | head -n 1
}

SSID=$(read_config ssid)
WPA_PASSPHRASE=$(read_config wpa_passphrase)
COUNTRY=$(read_config country_code)
CHANNEL=$(read_config channel)
[[ $SSID =~ ^[A-Za-z0-9._-]{1,32}$ ]] || die 'installed SSID is invalid'
[[ $WPA_PASSPHRASE =~ ^[A-Za-z0-9.!_-]{8,63}$ ]] || die 'installed Wi-Fi password is invalid'
[[ $COUNTRY =~ ^[A-Z]{2}$ ]] || die 'installed country code is invalid'
case "$CHANNEL" in 1|6|11) ;; *) die 'installed Wi-Fi channel is invalid' ;; esac

TIMESTAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_DIR=$BACKUP_ROOT/$TIMESTAMP
ARCHIVE=$BACKUP_DIR/system.tar
CONFIG_FILE=$BACKUP_DIR/install.conf
mkdir -p "$BACKUP_DIR"
printf '%s\n%s\n%s\n%s\n' "$SSID" "$WPA_PASSPHRASE" "$COUNTRY" "$CHANNEL" >"$CONFIG_FILE"
chmod 600 "$CONFIG_FILE"
unset WPA_PASSPHRASE

declare -a BACKUP_PATHS=(
    etc/zapret-rpi
    etc/NetworkManager/conf.d/10-zapret-rpi-wlan0.conf
    etc/systemd/network/20-zapret-rpi-wlan0.network
    etc/sysctl.d/90-zapret-rpi-router.conf
    etc/sudoers.d/zapret-rpi-web
    usr/local/lib/zapret-rpi
    opt/zapret2
    var/lib/zapret-rpi
)
if ((LEGACY_INSTALL == 0)); then
    BACKUP_PATHS+=(opt/zapret-rpi)
fi
for path in /etc/systemd/system/zapret-rpi-*.service /etc/systemd/system/zapret-rpi-*.timer \
    /etc/systemd/system/zapret2.service \
    /usr/local/sbin/zapret-rpi-*; do
    [[ -e $path || -L $path ]] && BACKUP_PATHS+=("${path#/}")
done
while IFS= read -r path; do
    BACKUP_PATHS+=("${path#/}")
done < <(find /etc/systemd/system -mindepth 2 -maxdepth 2 -type l \
    \( -name 'zapret-rpi-*' -o -name 'zapret2.service' \) -print)

tar --acls --xattrs --numeric-owner -cpf "$ARCHIVE" -C / "${BACKUP_PATHS[@]}"
if ((LEGACY_INSTALL)); then
    printf 'project=legacy\nupstream=%s\n' "$(git -C /opt/zapret2 rev-parse HEAD)" \
        >"$BACKUP_DIR/before.env"
else
    printf 'project=%s\nupstream=%s\n' \
        "$(tr -d '\r\n' <"$INSTALL_ROOT/VERSION")" \
        "$(tr -d '\r\n' <"$INSTALL_ROOT/UPSTREAM_COMMIT")" \
        >"$BACKUP_DIR/before.env"
fi

restore_snapshot() {
    echo "Restoring pre-update snapshot from $BACKUP_DIR" >&2
    systemctl stop zapret-rpi-autotune.service zapret-rpi-autocheck.timer \
        zapret-rpi-autocheck.service zapret-rpi-web-lan.service \
        zapret-rpi-web.service zapret2.service zapret-rpi-hostapd.service \
        zapret-rpi-dnsmasq.service zapret-rpi-nftables.service >/dev/null 2>&1 || true
    rm -rf -- /etc/zapret-rpi /usr/local/lib/zapret-rpi /opt/zapret2 \
        /opt/zapret-rpi /opt/zapret-rpi.new /var/lib/zapret-rpi
    rm -f -- /etc/NetworkManager/conf.d/10-zapret-rpi-wlan0.conf \
        /etc/systemd/network/20-zapret-rpi-wlan0.network \
        /etc/sysctl.d/90-zapret-rpi-router.conf /etc/sudoers.d/zapret-rpi-web
    find /usr/local/sbin -maxdepth 1 -type f -name 'zapret-rpi-*' -delete
    find /etc/systemd/system -maxdepth 2 \
        \( -name 'zapret-rpi-*' -o -name 'zapret2.service' \) -delete
    tar --acls --xattrs --numeric-owner -xpf "$ARCHIVE" -C /
    systemctl daemon-reload
    nmcli general reload >/dev/null 2>&1 || true
    networkctl reload >/dev/null 2>&1 || true
    sysctl --system >/dev/null 2>&1 || true
    /usr/local/lib/zapret-rpi/apply-nft.sh >/dev/null 2>&1 || true
    systemctl restart zapret-rpi-hostapd.service zapret-rpi-dnsmasq.service \
        zapret2.service zapret-rpi-web.service zapret-rpi-web-lan.service || true
    systemctl restart zapret-rpi-autocheck.timer >/dev/null 2>&1 || true
    systemctl restart zapret-rpi-nftables.service >/dev/null 2>&1 || true
    /usr/local/sbin/zapret-rpi-validate || true
}

on_error() {
    local rc=$?
    trap - ERR
    set +e
    restore_snapshot
    echo "Update failed and the previous installation was restored." >&2
    exit "$rc"
}
trap on_error ERR

echo "Snapshot saved to $BACKUP_DIR"
systemctl stop zapret-rpi-autocheck.timer zapret-rpi-autocheck.service >/dev/null 2>&1 || true
INSTALL_ARGS=(--config "$CONFIG_FILE" --update)
"$SOURCE_DIR/scripts/install.sh" "${INSTALL_ARGS[@]}"

STAGED=${INSTALL_ROOT}.new
rm -rf -- "$STAGED"
mkdir -p "$STAGED"
cp -a "$SOURCE_DIR/." "$STAGED/"
rm -rf -- "$STAGED/.git" "$STAGED/.env" "$STAGED/codex-state" \
    "$STAGED/web/frontend/node_modules"
find "$STAGED" -type d -name __pycache__ -prune -exec rm -rf -- {} +
chown -R root:root "$STAGED"
rm -rf -- "$INSTALL_ROOT"
mv "$STAGED" "$INSTALL_ROOT"

printf 'project=%s\nupstream=%s\n' \
    "$(tr -d '\r\n' <"$INSTALL_ROOT/VERSION")" \
    "$(tr -d '\r\n' <"$INSTALL_ROOT/UPSTREAM_COMMIT")" \
    >"$BACKUP_DIR/after.env"
printf '%s\n' "$BACKUP_DIR" >/var/lib/zapret-rpi/last-update-backup
chmod 600 /var/lib/zapret-rpi/last-update-backup
rm -f "$CONFIG_FILE"
trap - ERR

echo "Update completed successfully."
echo "Rollback snapshot retained at $BACKUP_DIR"
