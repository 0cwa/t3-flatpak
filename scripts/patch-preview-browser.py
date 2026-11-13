#!/usr/bin/env python3
"""Patch the reviewed AppImage launch sites; refuse changed upstream bundles."""

import hashlib
import json
from pathlib import Path
import shutil
import struct
import sys


SERVER = "apps/server/dist/server-bi3w5spO.mjs"
SERVER_SHA256 = "aa634dc27f7117c17e7c59944dd83b4fbfcbe8f09a06738be32cd96406a99435"
HELPER = "/app/lib/t3-preview/launch.mjs"
REPLACEMENTS = (
    ('const loadPlaywright = () => requirePlaywright("playwright-core");',
     'const loadPlaywright = () => isolatedPlaywright(requirePlaywright("playwright-core"));'),
    ('\t\t\texecutable,\n\t\t\tenv,\n\t\t\targs: [\n\t\t\t\t"--disable-gpu",',
     f'\t\t\texecutable: "{HELPER}",\n\t\t\tenv,\n\t\t\targs: [\n\t\t\t\t`--t3-browser-executable=${{executable}}`,\n\t\t\t\t"--disable-gpu",'),
    ('spawner.spawn(make$85(input.executable, [\n\t\t"--headless=new",',
     f'spawner.spawn(make$85("{HELPER}", [\n\t\t`--t3-browser-executable=${{input.executable}}`,\n\t\t"--headless=new",'),
    ('locator.setInputFiles([...input.paths],', 'locator.setInputFiles(await stageUploads(page, input.paths),'),
    ('open.chooser.setFiles([...input.paths],', 'open.chooser.setFiles(await stageUploads(tab.page, input.paths),'),
    ('open.chooser.setFiles(open.chooser.isMultiple() ? [...input.files] : input.files.slice(0, 1))',
     'open.chooser.setFiles(await stageUploads(tab.page, open.chooser.isMultiple() ? [...input.files] : input.files.slice(0, 1)))'),
    ('await download.saveAs(path);', 'await saveDownload(download, path);'),
)
ANDROID_MISSING_SDK_BEFORE = (
    "Android SDK was not found. Install it with Android Studio or set ANDROID_HOME to your SDK directory."
)
ANDROID_MISSING_SDK_AFTER = (
    "Android SDK is missing in this Flatpak. Check it with: "
    "flatpak run --command=t3code-android-diagnostics com.t3tools.t3code. "
    "Install with: flatpak run --command=t3code-android-setup com.t3tools.t3code "
    "--system-image 'system-images;android-36;google_apis;x86_64' "
    "(review and accept the SDK licenses when prompted)."
)


def patch_server(source):
    for before, after in REPLACEMENTS:
        if source.count(before) != 1:
            raise ValueError(f"Preview patch expected exactly one launch/transfer site: {before!r}")
        source = source.replace(before, after)
    if source.count(ANDROID_MISSING_SDK_BEFORE) != 1:
        raise ValueError("Android setup patch expected exactly one missing-SDK reason")
    source = source.replace(ANDROID_MISSING_SDK_BEFORE, ANDROID_MISSING_SDK_AFTER)
    imports = 'import { isolatedPlaywright, stageUploads, saveDownload } from "/app/lib/t3-preview/adapter.mjs";\n'
    if source.startswith("#!"):
        first, rest = source.split("\n", 1)
        return first + "\n" + imports + rest
    return imports + source


def read_header(stream):
    sizes = struct.unpack("<4I", stream.read(16))
    if sizes[0] != 4 or sizes[3] > 16 * 1024 * 1024:
        raise ValueError("Invalid asar header")
    return json.loads(stream.read(sizes[3])), 8 + sizes[1]


def entries(header, prefix=""):
    for name, entry in header.get("files", {}).items():
        filename = f"{prefix}/{name}" if prefix else name
        if "files" in entry:
            yield from entries(entry, filename)
        elif "offset" in entry:
            yield filename, entry


def rewrite_archive(filename, replacements):
    temporary = filename.with_name(filename.name + ".preview-patch")
    try:
        with filename.open("rb") as source:
            header, payload = read_header(source)
            files = sorted(entries(header), key=lambda item: int(item[1]["offset"]))
            offset = 0
            originals = []
            for name, entry in files:
                originals.append((name, int(entry["offset"]), entry["size"]))
                if name in replacements:
                    data = replacements[name]
                    entry["size"] = len(data)
                    if "integrity" in entry:
                        block = entry["integrity"]["blockSize"]
                        entry["integrity"] = {
                            "algorithm": "SHA256", "hash": hashlib.sha256(data).hexdigest(),
                            "blockSize": block,
                            "blocks": [hashlib.sha256(data[i:i + block]).hexdigest() for i in range(0, len(data), block)],
                        }
                entry["offset"] = str(offset)
                offset += entry["size"]
            encoded = json.dumps(header, separators=(",", ":"), ensure_ascii=False).encode()
            padding = b"\0" * (-(4 + len(encoded)) % 4)
            header_size = 4 + len(encoded) + len(padding)
            with temporary.open("wb") as target:
                target.write(struct.pack("<4I", 4, header_size + 4, header_size, len(encoded)))
                target.write(encoded + padding)
                for name, old_offset, size in originals:
                    if name in replacements:
                        target.write(replacements[name])
                    else:
                        source.seek(payload + old_offset)
                        remaining = size
                        while remaining:
                            data = source.read(min(remaining, 1024 * 1024))
                            if not data:
                                raise ValueError("Truncated asar payload")
                            target.write(data)
                            remaining -= len(data)
        shutil.copymode(filename, temporary)
        temporary.replace(filename)
    finally:
        temporary.unlink(missing_ok=True)


def patch_archive(filename):
    with filename.open("rb") as stream:
        header, payload = read_header(stream)
        files = dict(entries(header))
        entry = files.get(SERVER)
        if entry is None:
            raise ValueError("Unreviewed T3 bundle: preview server module moved")
        stream.seek(payload + int(entry["offset"]))
        data = stream.read(entry["size"])
    if hashlib.sha256(data).hexdigest() != SERVER_SHA256:
        raise ValueError("Unreviewed T3 bundle: preview server hash changed; review isolation integration before updating")
    rewrite_archive(filename, {SERVER: patch_server(data.decode()).encode()})


if __name__ == "__main__":
    try:
        patch_archive(Path(sys.argv[1]))
    except (OSError, ValueError, KeyError, IndexError, struct.error) as error:
        sys.exit(f"Preview isolation patch failed: {error}")
