#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly REPOSITORY="${ZAPRET_RPI_REPOSITORY:-larionovmike-collab/zapret-rpi}"
readonly REF="${ZAPRET_RPI_REF:-main}"
readonly INSTALL_ROOT="/opt/zapret-rpi"
TTY=/dev/tty
WORK_DIR=""
SOURCE_COMMITTED=0

log() { printf '[zapret-rpi] %s\n' "$*" >&2; }
die() { printf '[zapret-rpi] ERROR: %s\n' "$*" >&2; exit 1; }
cleanup() {
    [[ -z $WORK_DIR ]] || rm -rf -- "$WORK_DIR"
    ((SOURCE_COMMITTED)) || rm -rf -- "${INSTALL_ROOT}.new"
}
trap cleanup EXIT

prompt() {
    local variable=$1 label=$2 default=${3:-} value
    if [[ -n $default ]]; then
        printf '%s [%s]: ' "$label" "$default" >"$TTY"
    else
        printf '%s: ' "$label" >"$TTY"
    fi
    IFS= read -r value <"$TTY"
    printf -v "$variable" '%s' "${value:-$default}"
}

prompt_secret() {
    local variable=$1 label=$2 value
    printf '%s: ' "$label" >"$TTY"
    IFS= read -r -s value <"$TTY"
    printf '\n' >"$TTY"
    printf -v "$variable" '%s' "$value"
}

confirm() {
    local answer
    printf '%s [y/N]: ' "$1" >"$TTY"
    IFS= read -r answer <"$TTY"
    [[ $answer == y || $answer == Y || $answer == yes || $answer == YES ]]
}

normalize_source() {
    local source=$1 file
    while IFS= read -r -d '' file; do
        sed -i 's/\r$//' "$file"
    done < <(find "$source" -type f \( \
        -name '*.sh' -o -name '*.py' -o -name '*.service' -o -name '*.conf' \
        -o -name '*.in' -o -name '*.network' -o -name '*.md' -o -name '*.yml' \
        -o -name '*.yaml' -o -name '*.txt' -o -name '*.json' -o -name '*.js' \
        -o -name '*.jsx' -o -name '*.css' -o -name '*.html' \
        -o -name VERSION -o -name UPSTREAM_COMMIT -o -path '*/configs/zapret2/config' \
        \) -print0)
}

download_source() {
    local archive=$WORK_DIR/source.tar.gz source=$WORK_DIR/source
    local url="https://github.com/${REPOSITORY}/archive/refs/heads/${REF}.tar.gz"
    log "Downloading ${REPOSITORY}@${REF}"
    curl --proto '=https' --tlsv1.2 -fL --retry 3 --connect-timeout 15 --max-time 180 \
        "$url" -o "$archive"
    mkdir "$source"
    tar -xzf "$archive" --strip-components=1 -C "$source"
    normalize_source "$source"
    for required in VERSION UPSTREAM_COMMIT scripts/install.sh web/frontend/dist/index.html; do
        [[ -e $source/$required ]] || die "Downloaded source is incomplete: $required is missing."
    done
    printf '%s\n' "$source"
}

stage_installed_source() {
    local source=$1 staged="${INSTALL_ROOT}.new"
    rm -rf -- "$staged"
    mkdir -p "$staged"
    cp -a "$source/." "$staged/"
    rm -rf -- "$staged/.git" "$staged/.env" "$staged/codex-state" \
        "$staged/web/frontend/node_modules"
    find "$staged" -type d -name __pycache__ -prune -exec rm -rf -- {} +
    chown -R root:root "$staged"
}

[[ $EUID -eq 0 ]] || die "Run as root: curl ... | sudo bash"
[[ -r $TTY && -w $TTY ]] || die "An interactive terminal is required."
command -v curl >/dev/null || die "curl is required."
command -v tar >/dev/null || die "tar is required."
if [[ -e /var/lib/zapret-rpi/backup/original/manifest ]]; then
    if [[ -x /usr/local/sbin/zapret-rpi-validate || -f /etc/systemd/system/zapret2.service ]]; then
        die "zapret-rpi is already installed. Use update.sh instead."
    fi
    log "Reusing the original backup left by an earlier incomplete installation."
fi

WORK_DIR=$(mktemp -d)
SOURCE_DIR=$(download_source)
PROJECT_VERSION=$(tr -d '\r\n' <"$SOURCE_DIR/VERSION")
UPSTREAM_COMMIT=$(tr -d '\r\n' <"$SOURCE_DIR/UPSTREAM_COMMIT")

printf '\nzapret-rpi %s\n' "$PROJECT_VERSION" >"$TTY"
printf 'Upstream zapret2: %.12s\n\n' "$UPSTREAM_COMMIT" >"$TTY"
prompt AP_SSID "Wi-Fi access point name" "Zapret-RPi"
prompt_secret AP_PASSWORD "Wi-Fi password (8-63 characters: letters, digits, . ! _ -)"
prompt COUNTRY "Regulatory country code" "RU"
COUNTRY=${COUNTRY^^}
prompt CHANNEL "Wi-Fi channel: 1, 6 or 11" "6"

[[ $AP_SSID =~ ^[A-Za-z0-9._-]{1,32}$ ]] || die "Invalid Wi-Fi name."
[[ $AP_PASSWORD =~ ^[A-Za-z0-9.!_-]{8,63}$ ]] || die "Invalid Wi-Fi password."
[[ $COUNTRY =~ ^[A-Z]{2}$ ]] || die "Country must contain two uppercase letters."
case "$CHANNEL" in 1|6|11) ;; *) die "Channel must be 1, 6 or 11." ;; esac

printf '\nThe installer will take ownership of wlan0 and briefly restart network services.\n' >"$TTY"
confirm "Continue installation" || die "Installation cancelled."

CONFIG_FILE=$WORK_DIR/install.conf
printf '%s\n%s\n%s\n%s\n' "$AP_SSID" "$AP_PASSWORD" "$COUNTRY" "$CHANNEL" >"$CONFIG_FILE"
chmod 600 "$CONFIG_FILE"
unset AP_PASSWORD

stage_installed_source "$SOURCE_DIR"
chmod +x "$SOURCE_DIR"/scripts/*.sh
"$SOURCE_DIR/scripts/install.sh" --config "$CONFIG_FILE"

rm -rf -- "$INSTALL_ROOT"
mv "${INSTALL_ROOT}.new" "$INSTALL_ROOT"
SOURCE_COMMITTED=1
ETHERNET_IP=$(ip -4 -o addr show dev eth0 scope global | awk '{sub(/\/.*/, "", $4); print $4; exit}')

printf '\nInstallation completed.\n' >"$TTY"
printf 'Panel: http://%s\n' "$ETHERNET_IP" >"$TTY"
printf 'Wi-Fi: %s\n' "$AP_SSID" >"$TTY"
printf 'Update: curl -fsSL https://raw.githubusercontent.com/%s/refs/heads/main/update.sh | sudo bash\n' "$REPOSITORY" >"$TTY"
