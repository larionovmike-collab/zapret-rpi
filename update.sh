#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly REPOSITORY="${ZAPRET_RPI_REPOSITORY:-larionovmike-collab/zapret-rpi}"
readonly REF="${ZAPRET_RPI_REF:-main}"
readonly INSTALL_ROOT="/opt/zapret-rpi"
TTY=/dev/tty
WORK_DIR=""
ASSUME_YES=0
FORCE=0

log() { printf '[zapret-rpi] %s\n' "$*" >&2; }
die() { printf '[zapret-rpi] ERROR: %s\n' "$*" >&2; exit 1; }
cleanup() { [[ -z $WORK_DIR ]] || rm -rf -- "$WORK_DIR"; }
trap cleanup EXIT

usage() {
    cat <<'EOF'
Usage: sudo bash update.sh [--yes] [--force]
  --yes    do not ask for confirmation
  --force  reinstall even if VERSION is unchanged
EOF
}

while (($#)); do
    case "$1" in
        --yes|-y) ASSUME_YES=1 ;;
        --force) FORCE=1 ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; die "Unknown argument: $1" ;;
    esac
    shift
done

confirm() {
    local answer
    ((ASSUME_YES)) && return 0
    [[ -r $TTY && -w $TTY ]] || die "An interactive terminal is required unless --yes is used."
    printf '%s [y/N]: ' "$1" >"$TTY"
    IFS= read -r answer <"$TTY"
    [[ $answer == y || $answer == Y || $answer == yes || $answer == YES ]]
}

[[ $EUID -eq 0 ]] || die "Run as root."
[[ -r /var/lib/zapret-rpi/backup/original/manifest ]] || die "Original installation backup is missing."
if [[ -d $INSTALL_ROOT && -r $INSTALL_ROOT/VERSION && -r $INSTALL_ROOT/UPSTREAM_COMMIT ]]; then
    LEGACY_INSTALL=0
elif [[ -x /usr/local/sbin/zapret-rpi-validate && -d /opt/zapret2/.git ]]; then
    LEGACY_INSTALL=1
else
    die "Existing zapret-rpi installation was not found."
fi
command -v curl >/dev/null || die "curl is required."
command -v tar >/dev/null || die "tar is required."

WORK_DIR=$(mktemp -d)
ARCHIVE=$WORK_DIR/source.tar.gz
SOURCE_DIR=$WORK_DIR/source
URL="https://github.com/${REPOSITORY}/archive/refs/heads/${REF}.tar.gz"
log "Downloading ${REPOSITORY}@${REF}"
curl --proto '=https' --tlsv1.2 -fL --retry 3 --connect-timeout 15 --max-time 180 \
    "$URL" -o "$ARCHIVE"
mkdir "$SOURCE_DIR"
tar -xzf "$ARCHIVE" --strip-components=1 -C "$SOURCE_DIR"

for required in VERSION UPSTREAM_COMMIT scripts/install.sh scripts/update-system.sh web/frontend/dist/index.html; do
    [[ -e $SOURCE_DIR/$required ]] || die "Downloaded source is incomplete: $required is missing."
done

NEW_VERSION=$(tr -d '\r\n' <"$SOURCE_DIR/VERSION")
NEW_UPSTREAM=$(tr -d '\r\n' <"$SOURCE_DIR/UPSTREAM_COMMIT")
if ((LEGACY_INSTALL)); then
    CURRENT_VERSION=legacy
    CURRENT_UPSTREAM=$(git -C /opt/zapret2 rev-parse HEAD)
    log "An existing pre-repository installation will be adopted."
else
    CURRENT_VERSION=$(tr -d '\r\n' <"$INSTALL_ROOT/VERSION")
    CURRENT_UPSTREAM=$(tr -d '\r\n' <"$INSTALL_ROOT/UPSTREAM_COMMIT")
fi

printf 'Current project: %s, zapret2 %.12s\n' "$CURRENT_VERSION" "$CURRENT_UPSTREAM"
printf 'Available project: %s, zapret2 %.12s\n' "$NEW_VERSION" "$NEW_UPSTREAM"
if [[ $CURRENT_VERSION == "$NEW_VERSION" && $CURRENT_UPSTREAM == "$NEW_UPSTREAM" ]] && ((FORCE == 0)); then
    log "The installed version is already current. Use --force to reinstall it."
    exit 0
fi

confirm "Install the downloaded update" || die "Update cancelled."
chmod +x "$SOURCE_DIR"/scripts/*.sh
UPDATE_ARGS=(--source "$SOURCE_DIR")
if [[ -n ${SSH_CONNECTION:-} ]]; then
    SSH_CLIENT_ADDRESS=${SSH_CONNECTION%% *}
elif [[ -n ${SSH_CLIENT:-} ]]; then
    SSH_CLIENT_ADDRESS=${SSH_CLIENT%% *}
else
    SSH_CLIENT_ADDRESS=$(who -m 2>/dev/null | sed -n 's/.*(\([^()]*)).*$/\1/p' | head -n 1)
fi
[[ -n $SSH_CLIENT_ADDRESS ]] || die "Run the updater from an SSH session over Ethernet."
UPDATE_ARGS+=(--ssh-client "$SSH_CLIENT_ADDRESS")
"$SOURCE_DIR/scripts/update-system.sh" "${UPDATE_ARGS[@]}"
