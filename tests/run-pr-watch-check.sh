#!/usr/bin/env bash
# Test the installed reactor in a disposable app copy, without network or a model.
set -euo pipefail
tests_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
scratch_base=${T3_TEST_SCRATCH:-$HOME/agent-scratch/t3-flatpak}
mkdir -p -- "$scratch_base"
test_root=$(mktemp -d "$scratch_base/pr-watch-check.XXXXXXXX")
trap 'rm -rf -- "$test_root"' EXIT
app_location=$(flatpak info --user --show-location com.t3tools.t3code)
cp -a --reflink=auto "$app_location/files" "$test_root/app"
mkdir "$test_root/home"
python3 - "$test_root/app/bin/t3code/resources/app.asar" "$tests_dir" <<'PY'
import importlib.util
import os
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location("patch", Path(sys.argv[2]).parent / "scripts/patch-preview-browser.py")
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)
archive = Path(sys.argv[1])
with archive.open("rb") as stream:
    header, payload = patch.read_header(stream)
    entry = dict(patch.entries(header))[patch.SERVER]
    stream.seek(payload + int(entry["offset"]))
    source = stream.read(entry["size"]).decode()
if os.environ.get("T3_PR_WATCH_APPLY_PATCH") == "1":
    source = patch.patch_pr_watch(source)
    patch.rewrite_archive(archive, {patch.SERVER: source.encode()})
assert patch.PR_WATCH_READ_AFTER in source
assert patch.PR_WATCH_ACTIVITY_AFTER in source
assert patch.PR_WATCH_READ_BEFORE not in source
PY
python3 "$tests_dir/instrument-preview-browser.py" "$test_root/app/bin/t3code/resources/app.asar"
cp "$tests_dir/check-pr-watch.mjs" "$test_root/check-pr-watch.mjs"
flatpak run --user --app-path="$test_root/app" --sandbox --unshare=network \
    --nosocket=wayland --nosocket=x11 --nosocket=fallback-x11 \
    --filesystem="$test_root:ro" --env=HOME="$test_root/home" \
    --env=ELECTRON_RUN_AS_NODE=1 --command=/app/bin/t3code/t3code \
    com.t3tools.t3code "$test_root/check-pr-watch.mjs"
