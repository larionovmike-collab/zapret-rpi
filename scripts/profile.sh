#!/bin/bash
set -Eeuo pipefail

PROFILE_DIR=/etc/zapret-rpi/zapret2/profiles
ACTIVE=/etc/zapret-rpi/zapret2/active.conf

usage() { echo "Usage: $0 {list|get|set PROFILE}"; }
die() { echo "ERROR: $*" >&2; exit 1; }

profile_name() {
    local target
    target=$(readlink -f "$ACTIVE" 2>/dev/null || true)
    [[ $target == "$PROFILE_DIR/"*.conf ]] || return 1
    basename "$target" .conf
}

case ${1:-} in
    list)
        current=$(profile_name 2>/dev/null || true)
        for file in "$PROFILE_DIR"/*.conf; do
            [[ -e $file ]] || continue
            name=$(basename "$file" .conf)
            description=$(sed -n 's/^PROFILE_DESCRIPTION="\(.*\)"$/\1/p' "$file")
            [[ $name == "$current" ]] && active=true || active=false
            printf '%s\t%s\t%s\n' "$name" "$active" "$description"
        done
        ;;
    get)
        profile_name || die 'active profile is invalid'
        ;;
    set)
        [[ $EUID -eq 0 ]] || die 'set requires root'
        name=${2:-}
        [[ $name =~ ^[a-z0-9][a-z0-9-]{0,31}$ ]] || die 'invalid profile name'
        candidate=$PROFILE_DIR/$name.conf
        [[ -f $candidate && ! -L $candidate ]] || die 'unknown profile'
        bash -n /opt/zapret2/config "$candidate" || die 'profile syntax check failed'
        previous=$(readlink -f "$ACTIVE" 2>/dev/null || true)
        ln -s "$candidate" "$ACTIVE.new"
        mv -Tf "$ACTIVE.new" "$ACTIVE"
        if ! systemctl restart zapret2.service || ! systemctl is-active --quiet zapret2.service; then
            [[ -n $previous ]] && ln -s "$previous" "$ACTIVE.rollback" && mv -Tf "$ACTIVE.rollback" "$ACTIVE"
            systemctl restart zapret2.service || true
            die 'profile activation failed; previous profile restored'
        fi
        printf '%s\n' "$name"
        ;;
    *) usage >&2; exit 2 ;;
esac
