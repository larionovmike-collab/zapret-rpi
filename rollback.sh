#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

TTY=/dev/tty
ASSUME_YES=0

die() { printf '[zapret-rpi] ERROR: %s\n' "$*" >&2; exit 1; }

while (($#)); do
    case "$1" in
        --yes|-y) ASSUME_YES=1 ;;
        -h|--help)
            echo "Usage: sudo bash rollback.sh [--yes]"
            exit 0
            ;;
        *) die "Unknown argument: $1" ;;
    esac
    shift
done

[[ $EUID -eq 0 ]] || die "Run as root."
[[ -x /usr/local/sbin/zapret-rpi-rollback ]] || die "Installed rollback tool was not found."

if ((ASSUME_YES == 0)); then
    [[ -r $TTY && -w $TTY ]] || die "An interactive terminal is required unless --yes is used."
    printf 'Remove zapret-rpi and restore the pre-installation network configuration? [y/N]: ' >"$TTY"
    IFS= read -r answer <"$TTY"
    [[ $answer == y || $answer == Y || $answer == yes || $answer == YES ]] || die "Rollback cancelled."
fi

ARCHIVE_DIR=/var/backups/zapret-rpi-removed-$(date +%Y%m%d-%H%M%S)
mkdir -p "$ARCHIVE_DIR"
if [[ -d /var/lib/zapret-rpi/backup/original ]]; then
    cp -a /var/lib/zapret-rpi/backup/original "$ARCHIVE_DIR/"
fi

/usr/local/sbin/zapret-rpi-rollback
rm -rf -- /opt/zapret-rpi /opt/zapret-rpi.new /var/lib/zapret-rpi

echo "zapret-rpi was removed."
echo "The original installer backup was archived at $ARCHIVE_DIR"
