#!/usr/bin/env bash
# Run on a Flatpak-capable host, under a disposable OS account or CI container.
set -euo pipefail

if [[ $# != 1 || ${T3_DISPOSABLE_ACCOUNT:-} != 1 ]]; then
    echo "Usage: T3_DISPOSABLE_ACCOUNT=1 bash $0 /path/to/t3code.flatpak" >&2
    echo 'Use a disposable OS account: a separate installation still shares app data.' >&2
    exit 2
fi
bundle=$(realpath -- "$1")
[[ -f "$bundle" ]]
tests_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
for tool in flatpak flatpak-builder python3; do
    command -v "$tool" >/dev/null
done

test_root=$(mktemp -d)
export FLATPAK_USER_DIR="$test_root/installation"
app=com.t3tools.t3code
android_fixture_id="t3-android-ci-${BASHPID}-${RANDOM}-${RANDOM}"
android_xdg_home="$HOME/.var/app/$app/data/$android_fixture_id"
android_fixture_owned=0
cleanup() {
    local result=$?
    trap - EXIT
    if [[ "$android_fixture_owned" == 1 ]]; then
        case "$android_xdg_home" in
            "$HOME/.var/app/$app/data"/t3-android-ci-*) rm -rf -- "$android_xdg_home" ;;
            *) echo "Refusing to remove unexpected Android fixture path: $android_xdg_home" >&2; result=1 ;;
        esac
    fi
    rm -rf -- "$test_root"
    exit "$result"
}
trap cleanup EXIT
if [[ -e "$android_xdg_home" || -L "$android_xdg_home" ]]; then
    echo "Android launcher fixture path already exists: $android_xdg_home" >&2
    exit 1
fi
extension=com.t3tools.t3code.Tools.Test
extension_branch=26.08
flatpak remote-add --user --if-not-exists flathub \
    https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install --user --noninteractive "$bundle"

bash "$tests_dir/run-pr-watch-check.sh"
bash "$tests_dir/run-preview-browser-check.sh"

# Run the installed application's binaries, not the build SDK's copies.
flatpak run --user --command=sh "$app" -ec '
    for tool in make node npm corepack git gh glab tea; do
        "/app/bin/$tool" --version
    done
    /app/bin/ssh -V
'

# Verify the packaged helper's runtime dependencies and actual command wrappers.
flatpak run --user --command=sh "$app" -ec '
    test -x /app/bin/t3code-android-setup
    test -x /app/bin/t3code-android-diagnostics
    test -r /app/lib/t3-android/android_setup.py
    test -x /usr/bin/python3
    /usr/bin/python3 -c "import argparse, contextlib, fcntl, hashlib, os, pathlib, re, shutil, stat, subprocess, sys, tarfile, tempfile, urllib.error, urllib.request, uuid, xml.etree.ElementTree, zipfile"
'
flatpak run --user --command=t3code-android-setup "$app" --help >/dev/null
flatpak run --user --command=t3code-android-setup "$app" install-java --help >/dev/null

# A clean scratch XDG root makes diagnostics prove the installed missing-SDK
# guidance without touching app data, downloading packages, or accepting terms.
android_diag_data="$test_root/android-diagnostic-data"
mkdir -p -- "$android_diag_data"
diagnostic_status=0
if flatpak run --user --filesystem="$test_root:rw" --command=sh "$app" -ec '
    scratch=$1
    case "$scratch/" in "$2/"*) ;; *) exit 2 ;; esac
    export HOME="$scratch/home"
    export XDG_DATA_HOME="$scratch/data"
    export XDG_CONFIG_HOME="$scratch/config"
    export XDG_CACHE_HOME="$scratch/cache"
    export XDG_STATE_HOME="$scratch/state"
    unset ANDROID_HOME ANDROID_SDK_ROOT JAVA_HOME ANDROID_USER_HOME ANDROID_AVD_HOME
    /app/bin/t3code-android-diagnostics
' sh "$android_diag_data" "$test_root" \
    >"$test_root/android-diagnostics" 2>&1; then
    diagnostic_status=0
else
    diagnostic_status=$?
fi
[[ "$diagnostic_status" == 1 ]]
grep -Fq 't3code-android-diagnostics' "$test_root/android-diagnostics"
grep -Fq 't3code-android-setup' "$test_root/android-diagnostics"
[[ ! -e "$android_diag_data/data/t3code/android/sdk/current" ]]

# Check the actual installed extension contract before building the fixture.
flatpak info --user --show-metadata "$app" > "$test_root/metadata"
python3 - "$test_root/metadata" "$extension_branch" <<'PY'
import configparser
import sys

metadata = configparser.ConfigParser(interpolation=None)
metadata.read(sys.argv[1])
extension = metadata['Extension com.t3tools.t3code.Tools']
assert extension['version'] == sys.argv[2], 'Extension and fixture branches differ'
assert extension['directory'] == 'tools'
assert extension.getboolean('subdirectories')
assert extension.getboolean('no-autodownload')
assert extension['add-ld-path'] == 'lib'
PY

check_launcher() {
    flatpak run --user --filesystem="$tests_dir:ro" --command=bash \
        "$app" "$tests_dir/check-tool-extension.sh" "$1"
}

run_android_launcher_check() {
    flatpak run --user --filesystem="$tests_dir:ro" --command=bash \
        "$app" "$tests_dir/check-android-launcher.sh" "$1" "$android_fixture_id"
}

android_fixture_owned=1
run_android_launcher_check prepare
run_android_launcher_check verify

# Installing the app alone must neither install nor mount the fixture.
if flatpak info --user "$extension//$extension_branch" >/dev/null 2>&1; then
    echo 'The test extension was unexpectedly installed with the application.' >&2
    exit 1
fi
check_launcher absent

flatpak-builder --user --force-clean --install-deps-from=flathub \
    --disable-rofiles-fuse --state-dir="$test_root/state" \
    --repo="$test_root/repo" "$test_root/extension" \
    "$tests_dir/extension-fixture/com.t3tools.t3code.Tools.Test.yml"
flatpak remote-add --user --no-gpg-verify fixture "$test_root/repo"
flatpak install --user --noninteractive fixture "$extension//$extension_branch"

# The fixture has no rpath: successful execution requires Flatpak's loader setup.
flatpak run --user --command=sh "$app" -ec \
    '/app/tools/Test/bin/t3-tool-test; ldconfig -p | grep libt3tooltest'
check_launcher installed

flatpak uninstall --user --noninteractive "$extension//$extension_branch"
check_launcher absent
printf '%s\n' 'Installed, shared-library, and removed tool extension checks passed.'
