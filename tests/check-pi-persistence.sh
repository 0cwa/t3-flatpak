#!/usr/bin/env bash
# Exercise Pi home-directory persistence under the installed Flatpak.
# Use only a disposable OS account: FLATPAK_USER_DIR does not isolate app data.
set -euo pipefail

if [[ $# != 1 || ${T3_DISPOSABLE_ACCOUNT:-} != 1 ]]; then
    echo "Usage: T3_DISPOSABLE_ACCOUNT=1 bash $0 com.t3tools.t3code" >&2
    echo 'Use a disposable OS account; separate Flatpak installs share ~/.var/app.' >&2
    exit 2
fi
app=$1
if [[ "$app" != com.t3tools.t3code ]]; then
    echo "Unexpected Flatpak app ID: $app" >&2
    exit 2
fi
command -v flatpak >/dev/null
flatpak info --user "$app" >/dev/null

# A fake host Pi file must remain invisible even though sandbox-local ~/.pi
# is persistent. Never use an actual credential as the canary.
host_pi_created=0
if [[ ! -d "$HOME/.pi" ]]; then
    mkdir -p -- "$HOME/.pi"
    host_pi_created=1
fi
host_pi_canary=$(mktemp "$HOME/.pi/t3-flatpak-host-canary.XXXXXXXX")
printf '%s\n' 'dummy host Pi data; not a credential' > "$host_pi_canary"
fixture_root=$(mktemp -d)
fixture_name=$(basename -- "$fixture_root")
backing="$HOME/.var/app/$app/.pi/agent/.t3-flatpak-test-$fixture_name"
cleanup() {
    rm -f -- "$host_pi_canary"
    if [[ "$host_pi_created" == 1 ]]; then rmdir -- "$HOME/.pi" 2>/dev/null || true; fi
    rm -rf -- "$backing" "$fixture_root"
}
trap cleanup EXIT

# The first sandbox invocation writes representative settings, credentials,
# and session files. A unique fixture directory avoids editing Pi's real files.
flatpak run --user --command=sh "$app" -ec '
    set -eu
    if test -r "$2"; then
        echo "Host Pi canary is readable inside the sandbox" >&2
        exit 1
    fi
    dir="$HOME/.pi/agent/.t3-flatpak-test-$1"
    test ! -e "$dir"
    umask 077
    mkdir -p "$dir/sessions/fixture-project"
    printf "%s\n" "{\"theme\":\"fixture\"}" > "$dir/settings.json"
    printf "%s\n" "{\"token\":\"synthetic-not-a-real-credential\"}" > "$dir/auth.json"
    printf "%s\n" "{\"session\":\"fixture\"}" > "$dir/sessions/fixture-project/session.jsonl"
' sh "$fixture_name" "$host_pi_canary"

# The files must be backed by the *app-private* host directory, not ~/.pi.
for item in settings.json auth.json sessions/fixture-project/session.jsonl; do
    test -f "$backing/$item"
done
if [[ -e "$HOME/.pi/agent/.t3-flatpak-test-$fixture_name" ]]; then
    echo 'Pi fixture leaked into the real host home directory' >&2
    exit 1
fi
test "$(stat -c '%a' "$backing/auth.json")" = 600

# A fresh Flatpak invocation must read the exact same files without granting
# any additional filesystem or SSH-agent permission.
flatpak run --user --command=sh "$app" -ec '
    set -eu
    if test -r "$2"; then
        echo "Host Pi canary is readable after restarting the sandbox" >&2
        exit 1
    fi
    dir="$HOME/.pi/agent/.t3-flatpak-test-$1"
    test "$(cat "$dir/settings.json")" = "{\"theme\":\"fixture\"}"
    test "$(cat "$dir/auth.json")" = "{\"token\":\"synthetic-not-a-real-credential\"}"
    test "$(cat "$dir/sessions/fixture-project/session.jsonl")" = "{\"session\":\"fixture\"}"
' sh "$fixture_name" "$host_pi_canary"

printf '%s\n' 'Pi app-private settings, synthetic credentials, and sessions survive separate sandbox runs.'
