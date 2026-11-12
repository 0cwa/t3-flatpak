#!/usr/bin/env bash
# Run a test command on its own bus, including images without activation files.
set -euo pipefail

if [[ ${1:-} != --session ]]; then
    exec dbus-run-session -- bash "$0" --session "$@"
fi
shift
[[ $# -gt 0 ]]

portal_pid=""
cleanup() {
    if [[ -n "$portal_pid" ]]; then
        kill "$portal_pid" 2>/dev/null || true
        wait "$portal_pid" 2>/dev/null || true
    fi
}
trap cleanup EXIT

portal_version() {
    gdbus call --session --dest org.freedesktop.portal.Flatpak \
        --object-path /org/freedesktop/portal/Flatpak \
        --method org.freedesktop.DBus.Properties.Get \
        org.freedesktop.portal.Flatpak version
}

if ! version=$(portal_version); then
    # org.flatpak.Builder removes service activation files from its payload.
    for portal in /app/libexec/flatpak-portal /usr/libexec/flatpak-portal /usr/lib/flatpak/flatpak-portal; do
        if [[ -x "$portal" ]]; then
            "$portal" &
            portal_pid=$!
            break
        fi
    done
    if [[ -z "$portal_pid" ]]; then
        echo 'The test host has no Flatpak Spawn portal executable.' >&2
        exit 1
    fi
    gdbus wait --session --timeout 10 org.freedesktop.portal.Flatpak
    version=$(portal_version)
fi

if [[ ! $version =~ uint32\ ([0-9]+) ]] || (( BASH_REMATCH[1] < 3 )); then
    echo "The test host requires Flatpak Spawn portal version 3 or later; got $version" >&2
    exit 1
fi
printf 'Flatpak Spawn portal version %s\n' "${BASH_REMATCH[1]}"
"$@"
