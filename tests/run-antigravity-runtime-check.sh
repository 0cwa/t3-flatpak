#!/usr/bin/env bash
# Exercise the packaged Antigravity integration with an offline ACP fixture.
set -euo pipefail
tests_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
scratch_base=${T3_TEST_SCRATCH:-$HOME/agent-scratch/t3-flatpak}
mkdir -p -- "$scratch_base"
test_root=$(mktemp -d "$scratch_base/antigravity-check.XXXXXXXX")
trap 'rm -rf -- "$test_root"' EXIT
app_location=$(flatpak info --user --show-location com.t3tools.t3code)
cp -a --reflink=auto "$app_location/files" "$test_root/app"
mkdir "$test_root/home" "$test_root/tmp"

# Resolve test exports from the live server graph in a disposable archive.
python3 "$tests_dir/instrument-preview-browser.py" \
    "$test_root/app/bin/t3code/resources/app.asar" "$test_root"
python3 - "$test_root" <<'PYFIXTURE'
import io
from pathlib import Path
import sys
import tarfile

# The installed runtime must supply tar and its gzip support, not just the SDK.
with tarfile.open(Path(sys.argv[1]) / "antigravity-fixture.tar.gz", "w:gz") as fixture:
    data = b"Antigravity archive fixture\n\x00\xff\x80\n"
    member = tarfile.TarInfo("antigravity")
    member.size = len(data)
    member.mode = 0o755
    fixture.addfile(member, io.BytesIO(data))
PYFIXTURE
cp "$tests_dir/check-antigravity-runtime.mjs" "$test_root/check-antigravity-runtime.mjs"
flatpak run --user --app-path="$test_root/app" --sandbox --unshare=network \
    --nosocket=wayland --nosocket=x11 --nosocket=fallback-x11 \
    --filesystem="$test_root" --env=HOME="$test_root/home" \
    --env=TMPDIR="$test_root/tmp" --env=ELECTRON_RUN_AS_NODE=1 \
    --command=/app/bin/t3code/t3code com.t3tools.t3code \
    "$test_root/check-antigravity-runtime.mjs" "$test_root"
