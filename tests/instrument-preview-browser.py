#!/usr/bin/env python3
"""Expose shipped functions only in the disposable runtime-test archive."""

import importlib.util
from pathlib import Path
import sys

spec = importlib.util.spec_from_file_location("preview_patch", Path(__file__).resolve().parents[1] / "scripts/patch-preview-browser.py")
patch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(patch)
archive = Path(sys.argv[1])
exports = {
    patch.SERVER: (
        "\nexport {ServerBrowserContexts, captureHtmlScreenshot, measureHtmlHeights, setInputFiles};\n"
        "export const prWatchTest = {make:make$68, OrchestratorV2, ProjectionStoreV2, "
        "PullRequestService, SecureRandom, provideService, gen, succeed, fail, sync};\n"
    ),
    "apps/server/dist/ManagedEndpointRuntime-B-62Mn5X.mjs": "\nexport {layer$30 as previewTestNodeServices};\n",
}
replacements = {}
with archive.open("rb") as stream:
    header, payload = patch.read_header(stream)
    files = dict(patch.entries(header))
    for filename, suffix in exports.items():
        entry = files[filename]
        stream.seek(payload + int(entry["offset"]))
        original = stream.read(entry["size"])
        if filename == patch.SERVER and b"isolatedPlaywright(requirePlaywright" not in original:
            raise ValueError("The installed preview bundle has not been patched")
        replacements[filename] = original + suffix.encode()
patch.rewrite_archive(archive, replacements)
