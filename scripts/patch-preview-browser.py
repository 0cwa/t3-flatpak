#!/usr/bin/env python3
"""Patch reviewed AppImage integration sites; refuse changed upstream bundles."""

import hashlib
import json
from pathlib import Path
import shutil
import struct
import sys


SERVER = "apps/server/dist/server-8K5Ixyrs.mjs"
SERVER_SHA256 = "dd42a4f6ec75cf5cf32a1da225837656086fb4df3e03da7bd87fbd196412c8bf"
RUNTIME = "apps/server/dist/runtimeLayer-CyWe8jGq.mjs"
RUNTIME_SHA256 = "f341b92a0e63e6e11ae8a74699313bee310babd19d2e2a0901648040246218cb"
HELPER = "/app/lib/t3-preview/launch.mjs"
REPLACEMENTS = (
    ('const loadPlaywright = () => requirePlaywright("playwright-core");',
     'const loadPlaywright = () => isolatedPlaywright(requirePlaywright("playwright-core"));'),
    ('\t\t\texecutable,\n\t\t\tenv,\n\t\t\targs: [\n\t\t\t\t"--disable-gpu",',
     f'\t\t\texecutable: "{HELPER}",\n\t\t\tenv,\n\t\t\targs: [\n\t\t\t\t`--t3-browser-executable=${{executable}}`,\n\t\t\t\t"--disable-gpu",'),
    ('spawner.spawn(make$111(input.executable, [\n\t\t"--headless=new",',
     f'spawner.spawn(make$111("{HELPER}", [\n\t\t`--t3-browser-executable=${{input.executable}}`,\n\t\t"--headless=new",'),
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
ANDROID_HUB_SPAWN_BEFORE = '\t\t\t\thubTool.entryPath,\n\t\t\t\t"--port",'
ANDROID_HUB_SPAWN_AFTER = (
    '\t\t\t\t...(hostPlatform === "linux" ? ["--import", "/app/lib/t3-android/device-hub.mjs"] : []),\n'
    + ANDROID_HUB_SPAWN_BEFORE
)
ANDROID_SHUTDOWN_BEFORE = 'hubJson(request, HubActionResult, "shutdown")'
# Expo's Android shutdown waits up to 30 seconds after the console command.
ANDROID_SHUTDOWN_AFTER = 'hubJson(request, HubActionResult, "shutdown", device.platform === "android" ? seconds(45) : void 0)'


def patch_android_device_operations(source):
    if source.count(ANDROID_MISSING_SDK_BEFORE) != 1:
        raise ValueError("Android setup patch expected exactly one missing-SDK reason")
    source = source.replace(ANDROID_MISSING_SDK_BEFORE, ANDROID_MISSING_SDK_AFTER)
    for before, after in (
        (ANDROID_HUB_SPAWN_BEFORE, ANDROID_HUB_SPAWN_AFTER),
        (ANDROID_SHUTDOWN_BEFORE, ANDROID_SHUTDOWN_AFTER),
    ):
        if source.count(before) != 1:
            raise ValueError("Android device patch expected exactly one reviewed hub site")
        source = source.replace(before, after)
    return source

# T3 0.1.11 already keeps check delivery independent of comment API failures.
# The installed runtime test retains those regression cases without a patch.

# ACP clients must identify themselves as T3 Code for every runtime version.
# Google, not a substituted editor identity, determines the available models.
ANTIGRAVITY_CLIENT_BEFORE = '''/** Older releases offer non-Gemini models only to allowlisted clients such as Zed. */
function antigravityClientInfo(version$1) {
\tconst parsed = parseAntigravityVersion(version$1);
\treturn parsed !== void 0 && compareVersions(parsed, OPEN_MODEL_ACCESS_RELEASE) >= 0 ? {
\t\tname: "t3-code",
\t\ttitle: "T3 Code",
\t\tversion
\t} : {
\t\tname: "zed",
\t\tversion: "0.0.0"
\t};
}'''
ANTIGRAVITY_CLIENT_AFTER = '''function antigravityClientInfo() {
\treturn { name: "t3-code", title: "T3 Code", version };
}'''


def patch_antigravity_client(source):
    if source.count(ANTIGRAVITY_CLIENT_BEFORE) != 1:
        raise ValueError("Antigravity patch expected exactly one reviewed client identity")
    return source.replace(ANTIGRAVITY_CLIENT_BEFORE, ANTIGRAVITY_CLIENT_AFTER)


PR_WATCH_COMPLETION_REPLACEMENTS = (
    (
        '\t\tconst settled = failed.length === 0 && awaitingApproval.length === 0 && checks.every((check) => check.status !== "pending");\n'
        '\t\towedChecks = owed;',
        '\t\tconst settled = owedChecks.length > 0 && owedChecks.every((name) => {\n'
        '\t\t\tconst matching = checks.filter((check) => check.name === name);\n'
        '\t\t\treturn matching.length > 0 && matching.every((check) => check.status !== "pending" && check.status !== "action-required");\n'
        '\t\t});',
    ),
    (
        '\t\t} else if (settled && owed.length > 0) {\n'
        '\t\t\tchanges.push({\n'
        '\t\t\t\tkind: "checks-passed",\n'
        '\t\t\t\tcount: checks.length,\n'
        '\t\t\t\trequired: false\n'
        '\t\t\t});',
        '\t\t} else if (passedNow && settled) {\n'
        '\t\t\tchanges.push({\n'
        '\t\t\t\tkind: "remaining-checks-finished",\n'
        '\t\t\t\tchecks: checks.filter((check) => owedChecks.includes(check.name) || isFailedCheck(check))\n'
        '\t\t\t});',
    ),
    (
        '\t\tconst owed = passedNow && passed ? checks.filter((check) => check.required !== true && owedChecks.includes(check.name)).map((check) => check.name) : [];\n',
        '',
    ),
    (
        '\tcase "checks-passed": return [',
        '\tcase "remaining-checks-finished": return [\x60- Remaining checks finished${context.commit}. Required checks passed.\x60, '
        '...change.checks.map((check) => \x60  - ${check.name}: ${check.status}\x60)];\n'
        '\tcase "checks-passed": return [',
    ),
    (
        '\t"checks-passed": "checks passed",',
        '\t"remaining-checks-finished": "remaining checks finished",\n'
        '\t"checks-passed": "checks passed",',
    ),
)


def patch_pr_watch_completion(source):
    for before, after in PR_WATCH_COMPLETION_REPLACEMENTS:
        if source.count(before) != 1:
            raise ValueError("PR watch patch expected exactly one reviewed completion site")
        source = source.replace(before, after)
    return source


PR_WATCH_GOAL_BEFORE = (
    '\t\tconst commandNotice = noticeKind === "command" && command.creationSource === "server";\n'
    '\t\tif (!freshContext && (delegatedCompletion !== void 0 ? '
    'delegatedCompletion.taskIds.every((id) => projection.subagents.find((task) => '
    'task.id === id)?.completionWake === "always") : noticeKind === "delegated_task" || commandNotice)) {'
)
PR_WATCH_GOAL_AFTER = (
    '\t\tconst commandNotice = noticeKind === "command" && command.creationSource === "server";\n'
    '\t\tconst prWatchNotice = noticeKind === "monitor" && command.creationSource === "server" '
    '&& command.messageId.startsWith("message:pr-watch:");\n'
    '\t\tconst goalNotice = commandNotice || prWatchNotice;\n'
    '\t\tif (!freshContext && (delegatedCompletion !== void 0 ? '
    'delegatedCompletion.taskIds.every((id) => projection.subagents.find((task) => '
    'task.id === id)?.completionWake === "always") : noticeKind === "delegated_task" || goalNotice)) {'
)
PR_WATCH_GOAL_GATE_BEFORE = '(!commandNotice || providerThread.goal?.status === "active")'
PR_WATCH_GOAL_GATE_AFTER = '(!goalNotice || providerThread.goal?.status === "active")'


def patch_pr_watch_delivery(source):
    for before, after in (
        (PR_WATCH_GOAL_BEFORE, PR_WATCH_GOAL_AFTER),
        (PR_WATCH_GOAL_GATE_BEFORE, PR_WATCH_GOAL_GATE_AFTER),
    ):
        if source.count(before) != 1:
            raise ValueError("PR watch delivery patch expected exactly one reviewed routing site")
        source = source.replace(before, after)
    return source


def patch_server(source):
    for before, after in REPLACEMENTS:
        if source.count(before) != 1:
            raise ValueError(f"Preview patch expected exactly one launch/transfer site: {before!r}")
        source = source.replace(before, after)
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
        runtime_entry = files.get(RUNTIME)
        if runtime_entry is None:
            raise ValueError("Unreviewed T3 bundle: PR watch runtime module moved")
        stream.seek(payload + int(runtime_entry["offset"]))
        runtime_data = stream.read(runtime_entry["size"])
    if hashlib.sha256(data).hexdigest() != SERVER_SHA256:
        raise ValueError("Unreviewed T3 bundle: preview server hash changed; review isolation integration before updating")
    if hashlib.sha256(runtime_data).hexdigest() != RUNTIME_SHA256:
        raise ValueError("Unreviewed T3 bundle: PR watch runtime hash changed; review delivery before updating")
    rewrite_archive(filename, {
        SERVER: patch_antigravity_client(patch_server(data.decode())).encode(),
        RUNTIME: patch_android_device_operations(patch_pr_watch_delivery(patch_pr_watch_completion(runtime_data.decode()))).encode(),
    })


if __name__ == "__main__":
    try:
        patch_archive(Path(sys.argv[1]))
    except (OSError, ValueError, KeyError, IndexError, struct.error) as error:
        sys.exit(f"Preview isolation patch failed: {error}")
