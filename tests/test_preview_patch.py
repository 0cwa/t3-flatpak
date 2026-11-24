"""Check live-module discovery, integration contracts, and atomic asar rewrites."""

import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("preview_patch", ROOT / "scripts/patch-preview-browser.py")
PATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH)


def write_archive(path, modules):
    header = {"files": {}}
    payload = bytearray()
    for name, source in modules.items():
        data = source.encode() if isinstance(source, str) else source
        files = header["files"]
        parts = name.split("/")
        for part in parts[:-1]:
            files = files.setdefault(part, {"files": {}})["files"]
        files[parts[-1]] = {"offset": str(len(payload)), "size": len(data)}
        payload.extend(data)
    encoded = json.dumps(header).encode()
    padding = b"\0" * (-(4 + len(encoded)) % 4)
    size = 4 + len(encoded) + len(padding)
    path.write_bytes(struct.pack("<4I", 4, size + 4, size, len(encoded)) + encoded + padding + payload)


def integration_modules(suffix="new", symbol="901"):
    """Small contract fixtures with regenerated filenames and symbol numbers."""
    server = PATCH.MODULE_ROOT + f"server-{suffix}.mjs"
    runtime = PATCH.MODULE_ROOT + f"runtime-{suffix}.mjs"
    identity = PATCH.ANTIGRAVITY_CLIENT_BEFORE.replace("version$1", "version$18").replace(
        "\t\tversion\n", "\t\tversion$27\n"
    ).replace("compareVersions(", "compareVersions$33(")
    server_source = "\n".join([
        f'import {{ evaluatePullRequestWatch }} from "./runtime-{suffix}.mjs";',
        "const runServer = null; export { runServer };",
        *(before for before, _ in PATCH.REPLACEMENTS),
        f'spawner.spawn(make${symbol}(input.executable, [\n\t\t"--headless=new",',
        identity,
    ])
    runtime_source = "\n".join([
        "function evaluatePullRequestWatch() {}",
        "const makeDeviceHubOperations = () => {};",
        "const noticeKind = command.notification?.source.kind;",
        "const SCREENSHOT_TIMEOUT = seconds$8(20);",
        PATCH.PR_WATCH_GOAL_BEFORE, PATCH.PR_WATCH_GOAL_GATE_BEFORE,
        *(before for before, _ in PATCH.PR_WATCH_COMPLETION_REPLACEMENTS),
        PATCH.ANDROID_HUB_SPAWN_BEFORE, PATCH.ANDROID_SHUTDOWN_BEFORE,
        json.dumps(PATCH.ANDROID_MISSING_SDK_BEFORE),
    ])
    modules = {
        PATCH.ENTRYPOINT: 'await import("./cli.mjs");',
        PATCH.MODULE_ROOT + "cli.mjs": f'export {{ runServer }} from "./server-{suffix}.mjs"; import "./local-cli.mjs";',
        server: server_source,
        runtime: runtime_source,
        # The CLI imports a second runtime. The live server does not use it.
        PATCH.MODULE_ROOT + "local-cli.mjs": runtime_source,
        # Unreferenced chunks must not be selected just because anchors match.
        PATCH.MODULE_ROOT + "unused-server.mjs": server_source,
        PATCH.MODULE_ROOT + "unused-runtime.mjs": runtime_source,
    }
    return modules, server, runtime


class PreviewPatchTests(unittest.TestCase):
    def test_android_hub_patch_rejects_moved_or_duplicated_sites(self):
        sites = (PATCH.ANDROID_HUB_SPAWN_BEFORE, PATCH.ANDROID_SHUTDOWN_BEFORE)
        source = "\n".join((*sites, json.dumps(PATCH.ANDROID_MISSING_SDK_BEFORE), "const SCREENSHOT_TIMEOUT = seconds(20);"))
        result = PATCH.patch_android_device_operations(source)
        self.assertIn(PATCH.ANDROID_HUB_SPAWN_AFTER, result)
        self.assertIn(PATCH.ANDROID_SHUTDOWN_AFTER, result)
        for site in sites:
            with self.subTest(site=site):
                with self.assertRaisesRegex(ValueError, "exactly one reviewed hub site"):
                    PATCH.patch_android_device_operations(source.replace(site, "changed upstream"))
                with self.assertRaisesRegex(ValueError, "exactly one reviewed hub site"):
                    PATCH.patch_android_device_operations(source + site)

    def test_antigravity_identity_patch_rejects_moved_or_duplicated_sites(self):
        source = PATCH.ANTIGRAVITY_CLIENT_BEFORE
        result = PATCH.patch_antigravity_client(source)
        self.assertIn('name: "t3-code"', result)
        self.assertNotIn('name: "zed"', result)
        self.assertNotIn('OPEN_MODEL_ACCESS_RELEASE', result)
        for changed in ("changed upstream", source + source):
            with self.subTest(source=changed):
                with self.assertRaisesRegex(ValueError, "exactly one reviewed client identity"):
                    PATCH.patch_antigravity_client(changed)

    def test_pr_watch_goal_delivery_preserves_routing_guards(self):
        source = PATCH.PR_WATCH_GOAL_BEFORE + "\n" + PATCH.PR_WATCH_GOAL_GATE_BEFORE
        result = PATCH.patch_pr_watch_delivery(source)
        self.assertIn(PATCH.PR_WATCH_GOAL_AFTER, result)
        self.assertIn(PATCH.PR_WATCH_GOAL_GATE_AFTER, result)
        for site in (PATCH.PR_WATCH_GOAL_BEFORE, PATCH.PR_WATCH_GOAL_GATE_BEFORE):
            with self.subTest(site=site):
                with self.assertRaisesRegex(ValueError, "exactly one reviewed routing site"):
                    PATCH.patch_pr_watch_delivery(source.replace(site, "changed upstream"))
                with self.assertRaisesRegex(ValueError, "exactly one reviewed routing site"):
                    PATCH.patch_pr_watch_delivery(source + site)

    def test_renamed_chunks_and_renumbered_symbols_preserve_integration(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "app.asar"
            for suffix, symbol in (("first", "901"), ("renamed", "2076")):
                modules, server, runtime = integration_modules(suffix, symbol)
                # An unrelated implementation change must not create a version lock.
                modules[server] += "\nconst unrelatedNewFeature = true;\n"
                write_archive(archive, modules)
                resolved = PATCH.resolve_archive(archive)
                self.assertEqual((resolved["server"], resolved["runtime"]), (server, runtime))
                PATCH.patch_archive(archive)
                updated = PATCH.read_archive_modules(archive)
                self.assertIn(f'spawner.spawn(make${symbol}("{PATCH.HELPER}",', updated[server])
                self.assertIn('version: version$27', updated[server])
                self.assertNotIn('name: "zed"', updated[server])
                self.assertIn("const unrelatedNewFeature = true;", updated[server])
                self.assertIn("seconds$8(45)", updated[runtime])
                self.assertIn(PATCH.ANDROID_HUB_SPAWN_AFTER, updated[runtime])
                self.assertIn(PATCH.ANDROID_MISSING_SDK_AFTER, updated[runtime])
                self.assertIn(PATCH.PR_WATCH_COMPLETION_REPLACEMENTS[0][1], updated[runtime])
                self.assertIn(PATCH.PR_WATCH_GOAL_AFTER, updated[runtime])
                for filename in modules.keys() - {server, runtime}:
                    self.assertEqual(updated[filename], modules[filename])

    def test_changed_contracts_leave_the_entire_archive_untouched(self):
        modules, server, runtime = integration_modules()
        cases = (
            (server, PATCH.REPLACEMENTS[0][0], 'const loadPlaywright = () => require("different-library");'),
            (server, 'input.executable, [\n\t\t"--headless=new",', 'input.otherExecutable, [\n\t\t"--headless=new",'),
            (server, 'name: "zed"', 'name: "another-client"'),
            (runtime, PATCH.PR_WATCH_COMPLETION_REPLACEMENTS[0][0], "changed completion behavior"),
            (runtime, PATCH.PR_WATCH_GOAL_GATE_BEFORE, "changed delivery behavior"),
            (runtime, PATCH.ANDROID_HUB_SPAWN_BEFORE, "changed device hub arguments"),
            (runtime, "const SCREENSHOT_TIMEOUT = seconds$8(20);", "const SCREENSHOT_TIMEOUT = otherDuration(20);"),
            (server, PATCH.REPLACEMENTS[0][0], "/* " + PATCH.REPLACEMENTS[0][0] + " */"),
            (runtime, PATCH.PR_WATCH_GOAL_GATE_BEFORE, "`" + PATCH.PR_WATCH_GOAL_GATE_BEFORE + "`"),
        )
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "app.asar"
            for filename, before, after in cases:
                with self.subTest(site=before):
                    changed = {**modules, filename: modules[filename].replace(before, after)}
                    write_archive(archive, changed)
                    original = archive.read_bytes()
                    with self.assertRaises(ValueError):
                        PATCH.patch_archive(archive)
                    self.assertEqual(archive.read_bytes(), original)

    def test_missing_and_ambiguous_live_modules_are_rejected_atomically(self):
        modules, server, runtime = integration_modules()
        cases = []
        cases.append({key: value for key, value in modules.items() if key != PATCH.ENTRYPOINT})
        cases.append({**modules, PATCH.ENTRYPOINT: 'await import("./missing.mjs");'})
        cases.append({**modules, PATCH.ENTRYPOINT: modules[PATCH.ENTRYPOINT] + '\nimport "./unused-server.mjs";'})
        cases.append({**modules, server: modules[server] + '\nimport "./unused-runtime.mjs";'})
        cases.append({**modules, server: modules[server] + '\n' + PATCH.REPLACEMENTS[0][0]})
        cases.append({**modules, runtime: modules[runtime] + '\n' + PATCH.PR_WATCH_GOAL_GATE_BEFORE})
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "app.asar"
            for index, changed in enumerate(cases):
                with self.subTest(case=index):
                    write_archive(archive, changed)
                    original = archive.read_bytes()
                    with self.assertRaises(ValueError):
                        PATCH.patch_archive(archive)
                    self.assertEqual(archive.read_bytes(), original)

    def test_import_graph_ignores_non_code_and_nonliteral_imports(self):
        source = r'''#!/usr/bin/env node
import { fromSomething as renamed } from "./static.mjs";
import { renamed as from } from "./named-from.mjs";
export * as from from "./namespace.mjs";
import "./side-effect.mjs";
export { run } from "./reexport.mjs";
const actual = await import("./dynamic.mjs");
// import("./comment.mjs")
/* import "./block-comment.mjs"; */
const quote = 'import("./quoted.mjs")';
const regex = /import\("\.\/regex.mjs"\)/;
const template = `import("./template.mjs") ${`nested ${"import('./nested.mjs')"}`}`;
const computed = import("./computed-" + suffix);
const computedTemplate = import(`./computed-${suffix}.mjs`);
const stringDivision = "return" / 2;
const propertyDivision = object.return / 2;
export function documentation() { return "from ./description.mjs"; }
'''
        self.assertEqual(PATCH.module_imports(source), {
            "./static.mjs", "./named-from.mjs", "./namespace.mjs", "./side-effect.mjs",
            "./reexport.mjs", "./dynamic.mjs",
        })

    def test_an_additional_live_browser_boundary_is_rejected(self):
        modules, server, _ = integration_modules()
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "app.asar"
            for addition in ('const secondLoader = require("playwright");',
                             'const secondLaunch = ["--headless"];'):
                with self.subTest(addition=addition):
                    changed = {
                        **modules,
                        server: modules[server] + '\nimport "./new-browser.mjs";',
                        PATCH.MODULE_ROOT + "new-browser.mjs": addition,
                    }
                    write_archive(archive, changed)
                    original = archive.read_bytes()
                    with self.assertRaisesRegex(ValueError, "boundary in the live server"):
                        PATCH.patch_archive(archive)
                    self.assertEqual(archive.read_bytes(), original)

    def test_graph_rejects_missing_or_escaping_relative_imports(self):
        for source in ('import "./missing.mjs";', 'import "../../../outside.mjs";'):
            with self.subTest(source=source):
                with self.assertRaises(ValueError):
                    PATCH.active_module_paths({PATCH.ENTRYPOINT: source})

    def test_both_launch_paths_and_file_transfers_are_required(self):
        spawn = 'spawner.spawn(make$7(input.executable, [\n\t\t"--headless=new",'
        source = "#!/usr/bin/env node\n" + spawn + "\n" + "\n".join(
            before for before, _ in PATCH.REPLACEMENTS
        )
        result = PATCH.patch_server(source)
        self.assertTrue(result.startswith("#!/usr/bin/env node\nimport "))
        for _, replacement in PATCH.REPLACEMENTS:
            self.assertIn(replacement, result)
        self.assertIn(f'spawner.spawn(make$7("{PATCH.HELPER}",', result)
        for before in [spawn, *(before for before, _ in PATCH.REPLACEMENTS)]:
            with self.subTest(site=before):
                with self.assertRaises(ValueError):
                    PATCH.patch_server(source.replace(before, "changed upstream"))
                with self.assertRaises(ValueError):
                    PATCH.patch_server(source + before)

    def test_missing_sdk_reason_is_replaced_once_with_packaged_commands(self):
        source = "\n".join(
            [PATCH.ANDROID_HUB_SPAWN_BEFORE, PATCH.ANDROID_SHUTDOWN_BEFORE,
             json.dumps(PATCH.ANDROID_MISSING_SDK_BEFORE), "const SCREENSHOT_TIMEOUT = seconds(20);"]
        )
        result = PATCH.patch_android_device_operations(source)
        self.assertNotIn(PATCH.ANDROID_MISSING_SDK_BEFORE, result)
        self.assertIn(PATCH.ANDROID_MISSING_SDK_AFTER, result)
        self.assertIn("t3code-android-diagnostics", result)
        self.assertIn("t3code-android-setup", result)
        with self.assertRaisesRegex(ValueError, "missing-SDK reason"):
            PATCH.patch_android_device_operations(source.replace(PATCH.ANDROID_MISSING_SDK_BEFORE, "changed upstream"))
        with self.assertRaisesRegex(ValueError, "missing-SDK reason"):
            PATCH.patch_android_device_operations(source + json.dumps(PATCH.ANDROID_MISSING_SDK_BEFORE))

    def test_archive_rewrite_preserves_other_files_and_updates_integrity(self):
        with tempfile.TemporaryDirectory() as directory:
            archive = Path(directory) / "app.asar"
            header = {"files": {
                "first": {"size": 3, "offset": "0", "integrity": {"blockSize": 2}},
                "second": {"size": 4, "offset": "3"},
                "unpacked": {"size": 99, "unpacked": True},
            }}
            encoded = json.dumps(header).encode()
            padding = b"\0" * (-(4 + len(encoded)) % 4)
            size = 4 + len(encoded) + len(padding)
            archive.write_bytes(struct.pack("<4I", 4, size + 4, size, len(encoded)) + encoded + padding + b"oldkeep")
            PATCH.rewrite_archive(archive, {"first": b"replacement"})
            with archive.open("rb") as stream:
                updated, payload = PATCH.read_header(stream)
                files = updated["files"]
                self.assertEqual(files["first"]["size"], 11)
                self.assertEqual(files["second"]["offset"], "11")
                self.assertEqual(files["first"]["integrity"]["hash"], hashlib.sha256(b"replacement").hexdigest())
                self.assertEqual(len(files["first"]["integrity"]["blocks"]), 6)
                self.assertEqual(files["unpacked"], header["files"]["unpacked"])
                stream.seek(payload)
                self.assertEqual(stream.read(), b"replacementkeep")
            unchanged = archive.read_bytes()
            with self.assertRaises(ValueError):
                PATCH.patch_archive(archive)
            self.assertEqual(archive.read_bytes(), unchanged)


if __name__ == "__main__":
    unittest.main()
