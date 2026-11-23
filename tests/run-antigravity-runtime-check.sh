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

# Append test-only exports to a disposable archive. The installed app is never
# changed, and the actual production functions remain the functions under test.
python3 - "$test_root/app/bin/t3code/resources/app.asar" "$tests_dir" "$test_root" <<'PY'
import importlib.util
import io
import json
from pathlib import Path
import sys
import tarfile

spec = importlib.util.spec_from_file_location(
    "patch", Path(sys.argv[2]).parent / "scripts/patch-preview-browser.py"
)
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)
archive = Path(sys.argv[1])
managed = "apps/server/dist/ManagedEndpointRuntime-DBgt8khC.mjs"
schema = "apps/server/dist/Schema-BBjlTC3i.mjs"
exports = {
    patch.SERVER: (
        "\nexport const antigravityRuntimeTest = {"
        "prepareAntigravityProfile, buildAntigravityAcpSpawnInput, "
        "resolveAntigravityInstanceDirectories, antigravityClientInfo, "
        "antigravityAuthConfigIssue, isAntigravitySignInRequiredError, "
        "makeAntigravityAcpRuntime, ChildProcessSpawner, cliArchiveTarCommand, "
        "gen, scoped, map, catch:catch_, succeed};\n"
    ),
    managed: "\nexport const antigravityTestServices = mergeAll(layer$43, layerWebCrypto$1, layerWebCrypto$2);\n",
}
replacements = {}
with archive.open("rb") as stream:
    header, payload = patch.read_header(stream)
    files = dict(patch.entries(header))
    for filename, suffix in exports.items():
        entry = files[filename]
        stream.seek(payload + int(entry["offset"]))
        original = stream.read(entry["size"])
        if filename == patch.SERVER:
            assert patch.ANTIGRAVITY_CLIENT_AFTER.encode() in original, "Antigravity client identity patch is missing"
        else:
            assert b"const layer$43 = /*#__PURE__*/ provideMerge(layer$52," in original, "Reviewed Node services moved"
        replacements[filename] = original + suffix.encode()
patch.rewrite_archive(archive, replacements)
base = "/app/bin/t3code/resources/app.asar/"
(Path(sys.argv[3]) / "antigravity-test-bindings.mjs").write_text(
    "export {antigravityRuntimeTest as t} from " + json.dumps(base + patch.SERVER) + ";\n"
    "export {antigravityTestServices as services} from " + json.dumps(base + managed) + ";\n"
    "export {ls as runPromise, Xo as provide} from " + json.dumps(base + schema) + ";\n"
)
# The installed runtime must supply tar and its gzip support, not just the SDK.
with tarfile.open(Path(sys.argv[3]) / "antigravity-fixture.tar.gz", "w:gz") as fixture:
    data = b"Antigravity archive fixture\n\x00\xff\x80\n"
    member = tarfile.TarInfo("antigravity")
    member.size = len(data)
    member.mode = 0o755
    fixture.addfile(member, io.BytesIO(data))
PY
cp "$tests_dir/check-antigravity-runtime.mjs" "$test_root/check-antigravity-runtime.mjs"
flatpak run --user --app-path="$test_root/app" --sandbox --unshare=network \
    --nosocket=wayland --nosocket=x11 --nosocket=fallback-x11 \
    --filesystem="$test_root" --env=HOME="$test_root/home" \
    --env=TMPDIR="$test_root/tmp" --env=ELECTRON_RUN_AS_NODE=1 \
    --command=/app/bin/t3code/t3code com.t3tools.t3code \
    "$test_root/check-antigravity-runtime.mjs" "$test_root"
