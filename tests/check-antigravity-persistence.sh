#!/usr/bin/env bash
# Exercise Antigravity state persistence under the installed Flatpak.
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

fixture_root=$(mktemp -d)
fixture=".t3-flatpak-test-$(basename -- "$fixture_root")"
profile_key=$(printf '%s' "$fixture" | sha256sum | cut -d ' ' -f 1)
paths=(
    ".gemini/config/skills/$fixture"
    ".gemini/antigravity-cli/skills/$fixture"
    ".t3/tools/antigravity-acp/linux-x64/versions/$fixture"
    ".t3/userdata/providers/antigravity/$profile_key"
)
app_home="$HOME/.var/app/$app"
host_gemini_created=0
host_canary=""
cleanup() {
    if [[ -n "$host_canary" ]]; then rm -f -- "$host_canary"; fi
    if [[ "$host_gemini_created" == 1 ]]; then rmdir -- "$HOME/.gemini" 2>/dev/null || true; fi
    # The managed-runtime fixture deliberately retains upstream's read-only mode.
    if [[ -d "$app_home/${paths[2]}" ]]; then chmod u+w -- "$app_home/${paths[2]}"; fi
    for path in "${paths[@]}"; do rm -rf -- "$app_home/$path"; done
    rm -rf -- "$fixture_root"
}
trap cleanup EXIT

if [[ ! -d "$HOME/.gemini" ]]; then
    mkdir -p -- "$HOME/.gemini"
    host_gemini_created=1
fi
host_canary=$(mktemp "$HOME/.gemini/t3-flatpak-host-canary.XXXXXXXX")
printf '%s\n' 'dummy host Gemini data; not a credential' > "$host_canary"

# Use isolated fixture children of the real upstream paths. No active.json,
# registered provider profile, real executable, or credential is changed.
flatpak run --user --command=sh "$app" -ec '
    set -eu
    if test -r "$1"; then
        echo "Host Gemini canary is readable inside the sandbox" >&2
        exit 1
    fi
    shift
    for path do
        test ! -e "$HOME/$path"
        test ! -L "$HOME/$path"
    done
    umask 077
    mkdir -p "$HOME/$1" "$HOME/$2" "$HOME/$3" "$HOME/$4/antigravity-acp"
    for path in "$1" "$2"; do
        printf "%s\n" "synthetic skill fixture" > "$HOME/$path/SKILL.md"
    done
    printf "%s\n" "synthetic runtime fixture; never execute" > "$HOME/$3/antigravity"
    printf "%s\n" "{\"fixture\":true}" > "$HOME/$3/.install-complete.json"
    printf "%s\n" "{\"token\":\"synthetic-not-a-real-credential\"}" > "$HOME/$4/antigravity-acp/acp_token.json"
    chmod 555 "$HOME/$3/antigravity"
    chmod 400 "$HOME/$3/.install-complete.json"
    chmod 500 "$HOME/$3"
' sh "$host_canary" "${paths[@]}"

# The host must see the files only in Flatpak app data, not its own tool homes.
files=(SKILL.md SKILL.md antigravity antigravity-acp/acp_token.json)
for index in "${!paths[@]}"; do
    test -f "$app_home/${paths[$index]}/${files[$index]}"
    if [[ -e "$HOME/${paths[$index]}" || -L "$HOME/${paths[$index]}" ]]; then
        echo 'Antigravity fixture leaked into the real host home directory' >&2
        exit 1
    fi
done

# A new sandbox process must retain the contents and private/read-only modes.
flatpak run --user --command=sh "$app" -ec '
    set -eu
    if test -r "$1"; then
        echo "Host Gemini canary is readable after restarting the sandbox" >&2
        exit 1
    fi
    shift
    for path in "$1" "$2"; do
        test "$(cat "$HOME/$path/SKILL.md")" = "synthetic skill fixture"
    done
    test "$(cat "$HOME/$3/antigravity")" = "synthetic runtime fixture; never execute"
    test "$(cat "$HOME/$3/.install-complete.json")" = "{\"fixture\":true}"
    test "$(cat "$HOME/$4/antigravity-acp/acp_token.json")" = "{\"token\":\"synthetic-not-a-real-credential\"}"
    test "$(stat -c %a "$HOME/$3")" = 500
    test "$(stat -c %a "$HOME/$3/antigravity")" = 555
    test "$(stat -c %a "$HOME/$3/.install-complete.json")" = 400
    test "$(stat -c %a "$HOME/$4")" = 700
    test "$(stat -c %a "$HOME/$4/antigravity-acp")" = 700
    test "$(stat -c %a "$HOME/$4/antigravity-acp/acp_token.json")" = 600
' sh "$host_canary" "${paths[@]}"

printf '%s\n' 'Antigravity app-private skills, runtime markers, and synthetic credentials survive separate sandbox runs.'
