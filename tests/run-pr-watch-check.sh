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
    runtime_entry = dict(patch.entries(header))[patch.RUNTIME]
    stream.seek(payload + int(runtime_entry["offset"]))
    runtime = stream.read(runtime_entry["size"]).decode()
if os.environ.get("T3_PR_WATCH_APPLY_COMPLETION") == "1":
    runtime = patch.patch_pr_watch_completion(runtime)
    patch.rewrite_archive(archive, {patch.RUNTIME: runtime.encode()})
if os.environ.get("T3_PR_WATCH_APPLY_PATCH") == "1":
    runtime = patch.patch_pr_watch_delivery(runtime)
    patch.rewrite_archive(archive, {patch.RUNTIME: runtime.encode()})
# Upstream now handles detail and activity errors separately. A failed comment
# read must remain retryable without discarding the successful check read.
assert 'if (isFailure(detailRead)) {' in source
assert 'const detail = detailRead.value;' in source
assert 'const activity = isSuccess$1(activityRead) ? activityRead.value : none();' in source
assert 'remarksComplete: plan.activity ? remarks !== null : last?.remarksComplete ?? false,' in source
for _, after in patch.PR_WATCH_COMPLETION_REPLACEMENTS:
    if after:
        assert after in runtime
assert patch.PR_WATCH_GOAL_AFTER in runtime
assert patch.PR_WATCH_GOAL_GATE_AFTER in runtime
PY
python3 "$tests_dir/instrument-preview-browser.py" "$test_root/app/bin/t3code/resources/app.asar"
cp "$tests_dir/check-pr-watch.mjs" "$test_root/check-pr-watch.mjs"
flatpak run --user --app-path="$test_root/app" --sandbox --unshare=network \
    --nosocket=wayland --nosocket=x11 --nosocket=fallback-x11 \
    --filesystem="$test_root:ro" --env=HOME="$test_root/home" \
    --env=ELECTRON_RUN_AS_NODE=1 --command=/app/bin/t3code/t3code \
    com.t3tools.t3code "$test_root/check-pr-watch.mjs"
