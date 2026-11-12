#!/usr/bin/env bash
# Run through agent-heavy on a Flatpak-capable host. The parent has an empty home.
set -euo pipefail

tests_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
if [[ ${T3_PREVIEW_PORTAL_READY:-} != 1 ]]; then
    exec bash "$tests_dir/with-flatpak-portal.sh" env T3_PREVIEW_PORTAL_READY=1 bash "$0" "$@"
fi

scratch_base=${T3_TEST_SCRATCH:-$HOME/agent-scratch/t3-flatpak}
mkdir -p -- "$scratch_base"
test_root=$(mktemp -d "$scratch_base/preview-check.XXXXXXXX")
trap 'rm -rf -- "$test_root"' EXIT
app_location=$(flatpak info --user --show-location com.t3tools.t3code)
cp -a --reflink=auto "$app_location/files" "$test_root/app"
python3 - "$test_root/app/bin/t3code/resources/app.asar" "$tests_dir" <<'PY'
import importlib.util
from pathlib import Path
import sys

repository = Path(sys.argv[2]).resolve().parent
spec = importlib.util.spec_from_file_location(
    "preview_patch", repository / "scripts/patch-preview-browser.py"
)
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)
archive = Path(sys.argv[1])
with archive.open("rb") as stream:
    header, payload = patch.read_header(stream)
    entry = dict(patch.entries(header))[patch.SERVER]
    stream.seek(payload + int(entry["offset"]))
    server = stream.read(entry["size"]).decode()
assert patch.ANDROID_MISSING_SDK_AFTER in server
assert patch.ANDROID_MISSING_SDK_BEFORE not in server
assert "isolatedPlaywright" in server and "stageUploads" in server
print("Installed Devices error includes Android setup and diagnostics commands.")
PY

# Local development can test the packaging delta against an existing AppImage.
if [[ ${T3_PREVIEW_APPLY_PATCH:-0} == 1 ]]; then
    python3 "$tests_dir/../scripts/patch-preview-browser.py" "$test_root/app/bin/t3code/resources/app.asar"
    install -d "$test_root/app/lib/t3-preview"
    install -m644 "$tests_dir"/../preview/{adapter,paths,supervisor}.mjs "$test_root/app/lib/t3-preview/"
    install -m755 "$tests_dir/../preview/launch.mjs" "$test_root/app/lib/t3-preview/"
fi
python3 "$tests_dir/instrument-preview-browser.py" "$test_root/app/bin/t3code/resources/app.asar"
cp "$tests_dir/check-preview-browser.mjs" "$test_root/check.mjs"
mkdir "$test_root/state"

browser=${T3_TEST_BROWSER:-}
if [[ -z "$browser" ]]; then
    python3 - "$test_root" <<'PY'
import hashlib
from pathlib import Path
import sys
import urllib.request
import zipfile

root = Path(sys.argv[1])
archive = root / "browser.zip"
browser = root / "browser"
url = "https://storage.googleapis.com/chrome-for-testing-public/157.0.8103.38/linux64/chrome-headless-shell-linux64.zip"
digest = hashlib.sha256()
size = 0
with urllib.request.urlopen(url, timeout=60) as response, archive.open("wb") as output:
    while data := response.read(1024 * 1024):
        size += len(data)
        digest.update(data)
        output.write(data)
assert size == 128_168_542
assert digest.hexdigest() == "deafffa74660b34853a4119c361fd427ac8c9fcb62db51ddb8a797cd83a4ba2d"
with zipfile.ZipFile(archive) as source:
    for entry in source.infolist():
        assert entry.filename.startswith("chrome-headless-shell-linux64/")
        target = browser / entry.filename.removeprefix("chrome-headless-shell-linux64/")
        assert target.resolve().is_relative_to(browser.resolve())
        if entry.is_dir():
            target.mkdir(parents=True, exist_ok=True)
        else:
            assert entry.external_attr >> 16 & 0o170000 in (0, 0o100000)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(source.read(entry))
            target.chmod(entry.external_attr >> 16 & 0o777)
archive.unlink()
PY
    browser="$test_root/browser/chrome-headless-shell"
fi
browser=$(realpath -- "$browser")

flatpak run --user --app-path="$test_root/app" --sandbox --session-bus --share=network \
    --no-talk-name=org.freedesktop.Flatpak \
    --filesystem="$test_root" --filesystem="$(dirname -- "$browser"):ro" \
    --env=ELECTRON_RUN_AS_NODE=1 --env=TMPDIR="$test_root/state" \
    --command=/app/bin/t3code/t3code com.t3tools.t3code \
    "$test_root/check.mjs" "$test_root/state" "$browser"
