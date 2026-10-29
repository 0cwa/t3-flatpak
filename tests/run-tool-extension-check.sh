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
trap 'rm -rf -- "$test_root"' EXIT
export FLATPAK_USER_DIR="$test_root/installation"
app=com.t3tools.t3code
extension=com.t3tools.t3code.Tools.Test
extension_branch=26.08
flatpak remote-add --user --if-not-exists flathub \
    https://dl.flathub.org/repo/flathub.flatpakrepo
flatpak install --user --noninteractive "$bundle"

# Run the installed application's binaries, not the build SDK's copies.
flatpak run --user --command=sh "$app" -ec '
    for tool in make node npm corepack git gh glab tea; do
        "/app/bin/$tool" --version
    done
    /app/bin/ssh -V
'

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
