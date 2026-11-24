#!/usr/bin/env python3
"""Patch compatible AppImage integration sites before any archive mutation.

The manifest verifies the complete upstream AppImage. Here compatibility is a
contract over the live server modules and their integration sites, rather than
over incidental bundler filenames, symbol numbers, or unrelated module bytes.
"""

import hashlib
import json
from pathlib import Path
import posixpath
import re
import shutil
import struct
import sys


MODULE_ROOT = "apps/server/dist/"
ENTRYPOINT = MODULE_ROOT + "bin.mjs"
IDENTIFIER = r"[A-Za-z_$][A-Za-z0-9_$]*"
HELPER = "/app/lib/t3-preview/launch.mjs"
REPLACEMENTS = (
    ('const loadPlaywright = () => requirePlaywright("playwright-core");',
     'const loadPlaywright = () => isolatedPlaywright(requirePlaywright("playwright-core"));'),
    ('\t\t\texecutable,\n\t\t\tenv,\n\t\t\targs: [\n\t\t\t\t"--disable-gpu",',
     f'\t\t\texecutable: "{HELPER}",\n\t\t\tenv,\n\t\t\targs: [\n\t\t\t\t`--t3-browser-executable=${{executable}}`,\n\t\t\t\t"--disable-gpu",'),
    ('locator.setInputFiles([...input.paths],', 'locator.setInputFiles(await stageUploads(page, input.paths),'),
    ('open.chooser.setFiles([...input.paths],', 'open.chooser.setFiles(await stageUploads(tab.page, input.paths),'),
    ('open.chooser.setFiles(open.chooser.isMultiple() ? [...input.files] : input.files.slice(0, 1))',
     'open.chooser.setFiles(await stageUploads(tab.page, open.chooser.isMultiple() ? [...input.files] : input.files.slice(0, 1)))'),
    ('await download.saveAs(path);', 'await saveDownload(download, path);'),
)
PREVIEW_SPAWN_PATTERN = re.compile(
    r'spawner\.spawn\((?P<command>make(?:\$\d+)?)\(input\.executable, \[\n\t\t"--headless=new",'
)
ANDROID_DURATION_PATTERN = re.compile(
    r'const SCREENSHOT_TIMEOUT = (?P<seconds>seconds(?:\$\d+)?)\(20\);'
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
    durations = syntax_matches(source, ANDROID_DURATION_PATTERN)
    if len(durations) != 1:
        raise ValueError("Android device patch expected exactly one duration helper")
    shutdown = ANDROID_SHUTDOWN_AFTER.replace("seconds(45)", durations[0]["seconds"] + "(45)")
    return apply_contracts(source, (
        (json.dumps(ANDROID_MISSING_SDK_BEFORE), json.dumps(ANDROID_MISSING_SDK_AFTER)),
        (ANDROID_HUB_SPAWN_BEFORE, ANDROID_HUB_SPAWN_AFTER),
        (ANDROID_SHUTDOWN_BEFORE, shutdown),
    ), "Android device patch expected exactly one reviewed hub site / missing-SDK reason")

# T3 0.1.11 already keeps check delivery independent of comment API failures.
# The installed runtime test retains those regression cases without a patch.

# ACP clients must identify themselves as T3 Code for every runtime version.
# Google, not a substituted editor identity, determines the available models.
ANTIGRAVITY_CLIENT_COMMENT = "/** Older releases offer non-Gemini models only to allowlisted clients such as Zed. */\n"
ANTIGRAVITY_CLIENT_BEFORE = '''function antigravityClientInfo(version$1) {
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
ANTIGRAVITY_CLIENT_PATTERN = re.compile(
    re.escape(ANTIGRAVITY_CLIENT_BEFORE)
    .replace(re.escape("version$1"), r"(?P<argument>version(?:\$\d+)?)", 1)
    .replace(re.escape("version$1"), r"(?P=argument)")
    .replace(re.escape("\t\tversion\n"), r"\t\t(?P<version>version(?:\$\d+)?)\n")
    .replace(re.escape("compareVersions"), r"compareVersions(?:\$\d+)?")
)


def patch_antigravity_client(source):
    matches = syntax_matches(source, ANTIGRAVITY_CLIENT_PATTERN)
    declarations = syntax_matches(source, re.compile(r"\bfunction antigravityClientInfo\("))
    if len(matches) != 1 or len(declarations) != 1:
        raise ValueError("Antigravity patch expected exactly one reviewed client identity")
    match = matches[0]
    after = ANTIGRAVITY_CLIENT_AFTER.replace(
        ', version };', ', version: ' + match["version"] + ' };'
    ) if match["version"] != "version" else ANTIGRAVITY_CLIENT_AFTER
    prefix = source[:match.start()]
    if prefix.endswith(ANTIGRAVITY_CLIENT_COMMENT):
        prefix = prefix[:-len(ANTIGRAVITY_CLIENT_COMMENT)]
    return prefix + after + source[match.end():]


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
    return apply_contracts(source, PR_WATCH_COMPLETION_REPLACEMENTS,
                           "PR watch patch expected exactly one reviewed completion site")


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
    return apply_contracts(source, (
        (PR_WATCH_GOAL_BEFORE, PR_WATCH_GOAL_AFTER),
        (PR_WATCH_GOAL_GATE_BEFORE, PR_WATCH_GOAL_GATE_AFTER),
    ), "PR watch delivery patch expected exactly one reviewed routing site")


def patch_server(source):
    matches = syntax_matches(source, PREVIEW_SPAWN_PATTERN)
    if len(matches) != 1:
        raise ValueError("Preview patch expected exactly one headless browser launch site")
    match = matches[0]
    replacement = (
        f'spawner.spawn({match["command"]}("{HELPER}", [\n'
        '\t\t`--t3-browser-executable=${input.executable}`,\n\t\t"--headless=new",'
    )
    source = source[:match.start()] + replacement + source[match.end():]
    source = apply_contracts(source, REPLACEMENTS,
                             "Preview patch expected exactly one launch/transfer site")
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


def read_archive_modules(filename):
    """Read compiled server modules without executing their code."""
    modules = {}
    with Path(filename).open("rb") as stream:
        header, payload = read_header(stream)
        for name, entry in entries(header):
            if not name.startswith(MODULE_ROOT) or not name.endswith(".mjs"):
                continue
            stream.seek(payload + int(entry["offset"]))
            data = stream.read(entry["size"])
            if len(data) != entry["size"]:
                raise ValueError(f"Truncated server module: {name}")
            modules[name] = data.decode("utf-8")
    return modules


_WORD = re.compile(IDENTIFIER)
_REGEX_PREFIX = frozenset((
    None, "=", "(", "[", "{", ",", ";", ":", "!", "?", "&", "|", "+", "-",
    "*", "%", "~", "<", ">", "return", "throw", "case", "yield", "typeof", "void",
    "delete", "in", "of", "instanceof", "else", "do", "...",
))


def _quoted_end(source, start, quote):
    index = start + 1
    while index < len(source):
        if source[index] == "\\":
            index += 2
        elif source[index] == quote:
            return index + 1
        elif quote == "`" and source.startswith("${", index):
            index = _template_expression_end(source, index + 2)
        else:
            index += 1
    raise ValueError("Unterminated JavaScript literal while resolving module imports")


def _template_expression_end(source, start):
    depth = 1
    previous = None
    index = start
    while index < len(source):
        kind, value, index = _next_js_token(source, index, previous)
        if kind == "code":
            depth += (value == "{") - (value == "}")
            if depth == 0:
                return index
        previous = _token_context(kind, value, previous)
    raise ValueError("Unterminated template expression while resolving module imports")


def _next_js_token(source, index, previous):
    """Read enough JavaScript lexical structure to ignore non-code imports.

    This reads the bundler's literal ESM imports; it does not evaluate dynamic
    expressions. Templates (including nested interpolations), comments, quoted
    strings, and regular expressions cannot manufacture an import edge.
    """
    while index < len(source):
        char = source[index]
        if char.isspace():
            index += 1
        elif source.startswith("//", index) or (index == 0 and source.startswith("#!")):
            newline = source.find("\n", index + 2)
            index = len(source) if newline == -1 else newline + 1
        elif source.startswith("/*", index):
            end = source.find("*/", index + 2)
            if end == -1:
                raise ValueError("Unterminated JavaScript comment")
            index = end + 2
        else:
            break
    if index == len(source):
        return "end", "", index
    char = source[index]
    if char in "\"'`":
        end = _quoted_end(source, index, char)
        return ("template" if char == "`" else "string"), source[index + 1:end - 1], end
    if char == "/" and previous in _REGEX_PREFIX:
        start = index
        index += 1
        character_class = False
        while index < len(source):
            char = source[index]
            if char == "\\":
                index += 2
                continue
            if char == "[":
                character_class = True
            elif char == "]":
                character_class = False
            elif char == "/" and not character_class:
                index += 1
                while index < len(source) and source[index].isalpha():
                    index += 1
                return "regex", source[start:index], index
            elif char in "\r\n":
                raise ValueError("Unsupported JavaScript regular expression while resolving imports")
            index += 1
        raise ValueError("Unterminated JavaScript regular expression")
    word = _WORD.match(source, index)
    if word:
        return "code", word.group(), word.end()
    for operator in ("...", "++", "--", "?."):
        if source.startswith(operator, index):
            return "code", operator, index + len(operator)
    return "code", char, index + 1


def _token_context(kind, value, previous):
    if kind != "code" or (previous in (".", "?.") and _WORD.fullmatch(value)):
        return "<value>"
    return value


def _syntax_offsets(source, wanted):
    """Locate token starts, excluding lookalikes in comments and literals."""
    if not wanted:
        return set()
    last = max(wanted)
    found = set()
    index = 0
    previous = None
    while index <= last and index < len(source):
        kind, value, index = _next_js_token(source, index, previous)
        start = index - len(value) - (2 if kind == "string" else 0)
        if kind in ("code", "string") and start in wanted:
            found.add(start)
        previous = _token_context(kind, value, previous)
    return found


def _match_start(match):
    return match.start() + len(match[0]) - len(match[0].lstrip())


def syntax_matches(source, pattern):
    matches = list(pattern.finditer(source))
    starts = _syntax_offsets(source, {_match_start(match) for match in matches})
    return [match for match in matches if _match_start(match) in starts]


def apply_contracts(source, replacements, error):
    """Apply exact, unique code contracts together; ignore quoted lookalikes."""
    groups = [(list(re.finditer(re.escape(before), source)), after) for before, after in replacements]
    starts = _syntax_offsets(source, {
        _match_start(match) for matches, _ in groups for match in matches
    })
    edits = []
    for matches, after in groups:
        matches = [match for match in matches if _match_start(match) in starts]
        if len(matches) != 1:
            raise ValueError(error)
        edits.append((matches[0].start(), matches[0].end(), after))
    edits.sort(reverse=True)
    boundary = len(source)
    for start, end, after in edits:
        if end > boundary:
            raise ValueError("Overlapping integration patch contracts")
        source = source[:start] + after + source[end:]
        boundary = start
    return source


def module_imports(source):
    """Return literal static, re-export, and dynamic ESM dependencies."""
    dependencies = set()
    index = 0
    previous = None
    declaration = None
    candidate = None
    braces = 0
    while index < len(source):
        kind, value, index = _next_js_token(source, index, previous)
        if declaration == "import":
            if kind == "string":
                dependencies.add(value)
                declaration = None
            elif kind == "code" and value == "(":
                declaration = "dynamic"
            elif kind == "code" and (value in ("{", "*") or _WORD.fullmatch(value)):
                declaration = "static"
                braces = int(value == "{")
            else:
                declaration = None
        elif declaration == "dynamic":
            candidate = value if kind == "string" else None
            declaration = "dynamic-end" if candidate is not None else None
        elif declaration == "dynamic-end":
            if kind == "code" and value in (")", ","):
                dependencies.add(candidate)
            declaration = None
        elif declaration == "export":
            declaration = "static" if kind == "code" and value in ("{", "*") else None
            braces = int(value == "{")
        elif declaration == "static":
            if kind == "code" and value == "{":
                braces += 1
            elif kind == "code" and value == "}":
                braces -= 1
            elif kind == "code" and value == "from" and braces == 0 and previous != "as":
                declaration = "from"
            elif kind == "code" and value == ";":
                declaration = None
        elif declaration == "from":
            if kind != "string":
                raise ValueError("Expected a literal module specifier after from")
            dependencies.add(value)
            declaration = None
        elif kind == "code" and value in ("import", "export") and previous not in (".", "?."):
            declaration = value
        previous = _token_context(kind, value, previous)
    return dependencies


def active_module_paths(modules, entry=ENTRYPOINT, _dependencies=None):
    """Follow literal imports from the packaged CLI, ignoring unused chunks."""
    if entry not in modules:
        raise ValueError(f"T3 server entrypoint is missing: {entry}")
    pending = [entry]
    active = set()
    dependencies = {} if _dependencies is None else _dependencies
    while pending:
        name = pending.pop()
        if name in active:
            continue
        active.add(name)
        if name not in dependencies:
            try:
                dependencies[name] = module_imports(modules[name])
            except ValueError as error:
                raise ValueError(f"Cannot resolve imports in {name}: {error}") from error
        for specifier in dependencies[name]:
            if not specifier.startswith("."):
                continue
            if "\\" in specifier or "?" in specifier or "#" in specifier:
                raise ValueError(f"Unsupported local module import in {name}: {specifier}")
            target = posixpath.normpath(posixpath.join(posixpath.dirname(name), specifier))
            if not target.startswith(MODULE_ROOT):
                raise ValueError(f"Server module import escapes its bundle: {target}")
            if target not in modules:
                raise ValueError(f"Server module dependency is missing: {target}")
            pending.append(target)
    return frozenset(active)


def _unique_module(modules, paths, anchors, label):
    patterns = [re.compile(re.escape(anchor)) if isinstance(anchor, str) else anchor for anchor in anchors]
    candidates = []
    for name in paths:
        source = modules[name]
        matches = [list(pattern.finditer(source)) for pattern in patterns]
        if not all(matches):
            continue
        starts = _syntax_offsets(source, {_match_start(match) for group in matches for match in group})
        if all(sum(_match_start(match) in starts for match in group) == 1 for group in matches):
            candidates.append(name)
    if len(candidates) != 1:
        raise ValueError(f"Expected exactly one active {label} module; found {len(candidates)}")
    return candidates[0]


def _check_browser_boundaries(modules, paths, server):
    # Matching the old sites must not silently accept a newly added, separate
    # Playwright loader or native headless launch in the same server graph.
    for pattern, label in (
        (re.compile(r'''(?P<quote>["'])(?:playwright|playwright-core|playwright-chromium)(?P=quote)'''), "Playwright dependency"),
        (re.compile(r'''(?P<quote>["'])--headless(?:=[^"']*)?(?P=quote)'''), "native headless launch"),
    ):
        owners = [name for name in paths for _ in syntax_matches(modules[name], pattern)]
        if owners != [server]:
            raise ValueError(f"Expected exactly one reviewed {label} boundary in the live server")


def resolve_modules(modules):
    """Share the live module/source map with disposable runtime instrumentation."""
    dependencies = {}
    active = active_module_paths(modules, _dependencies=dependencies)
    server = _unique_module(modules, active, (
        re.compile(r"\bexport \{ (?:runServer|runServer\$\d+ as runServer) \};"),
        "const loadPlaywright =", "function antigravityClientInfo(",
    ), "preview server")
    # A second runtime is also reachable through local CLI commands. Only the
    # runtime actually imported by the selected server can carry these patches.
    server_paths = active_module_paths(modules, server, _dependencies=dependencies)
    _check_browser_boundaries(modules, server_paths, server)
    runtime = _unique_module(modules, server_paths, (
        "const noticeKind = command.notification?.source.kind;",
        "const makeDeviceHubOperations =", "function evaluatePullRequestWatch(",
    ), "PR watch / Android runtime")
    return {"server": server, "runtime": runtime, "active_paths": active, "sources": modules}


def resolve_archive(filename):
    return resolve_modules(read_archive_modules(filename))


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
    resolved = resolve_archive(filename)
    server, runtime = resolved["server"], resolved["runtime"]
    sources = resolved["sources"]
    # Resolve and validate every patch in memory first. A changed contract must
    # leave the complete original archive untouched, including its integrity.
    replacements = {
        server: patch_antigravity_client(patch_server(sources[server])).encode(),
        runtime: patch_android_device_operations(patch_pr_watch_delivery(patch_pr_watch_completion(sources[runtime]))).encode(),
    }
    rewrite_archive(Path(filename), replacements)


if __name__ == "__main__":
    try:
        patch_archive(Path(sys.argv[1]))
    except (OSError, ValueError, KeyError, IndexError, struct.error) as error:
        sys.exit(f"Preview isolation patch failed: {error}")
