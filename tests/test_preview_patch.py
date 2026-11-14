"""Guard the two launch paths and asar offset/integrity rewrites."""

import hashlib
import importlib.util
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("preview_patch", ROOT / "scripts/patch-preview-browser.py")
PATCH = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PATCH)


class PreviewPatchTests(unittest.TestCase):
    def test_pr_watch_reads_checks_independently_of_comments(self):
        source = PATCH.PR_WATCH_READ_BEFORE + "\n" + PATCH.PR_WATCH_ACTIVITY_BEFORE
        result = PATCH.patch_pr_watch(source)
        self.assertIn(PATCH.PR_WATCH_READ_AFTER, result)
        self.assertIn(PATCH.PR_WATCH_ACTIVITY_AFTER, result)
        for site in (PATCH.PR_WATCH_READ_BEFORE, PATCH.PR_WATCH_ACTIVITY_BEFORE):
            with self.subTest(site=site):
                with self.assertRaisesRegex(ValueError, "exactly one reviewed read site"):
                    PATCH.patch_pr_watch(source.replace(site, "changed upstream"))
                with self.assertRaisesRegex(ValueError, "exactly one reviewed read site"):
                    PATCH.patch_pr_watch(source + site)

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

    def test_archive_rejects_changed_runtime_before_rewriting_either_module(self):
        scratch = Path.home() / "agent-scratch/t3-flatpak/preview-unit-tests"
        scratch.mkdir(parents=True, exist_ok=True)
        server = "\n".join([
            *(before for before, _ in PATCH.REPLACEMENTS), PATCH.ANDROID_MISSING_SDK_BEFORE,
            PATCH.PR_WATCH_READ_BEFORE, PATCH.PR_WATCH_ACTIVITY_BEFORE,
        ]).encode()
        runtime = (PATCH.PR_WATCH_GOAL_BEFORE + "\n" + PATCH.PR_WATCH_GOAL_GATE_BEFORE).encode()
        header = {"files": {
            "server": {"offset": "0", "size": len(server)},
            "runtime": {"offset": str(len(server)), "size": len(runtime)},
        }}
        encoded = json.dumps(header).encode()
        padding = b"\0" * (-(4 + len(encoded)) % 4)
        size = 4 + len(encoded) + len(padding)
        original = struct.pack("<4I", 4, size + 4, size, len(encoded)) + encoded + padding + server + runtime
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
            archive = Path(directory) / "app.asar"
            archive.write_bytes(original)
            with mock.patch.multiple(PATCH, SERVER="server", RUNTIME="runtime",
                                     SERVER_SHA256=hashlib.sha256(server).hexdigest(),
                                     RUNTIME_SHA256="changed"):
                with self.assertRaisesRegex(ValueError, "runtime hash changed"):
                    PATCH.patch_archive(archive)
            self.assertEqual(archive.read_bytes(), original)
            with mock.patch.multiple(PATCH, SERVER="server", RUNTIME="runtime",
                                     SERVER_SHA256=hashlib.sha256(server).hexdigest(),
                                     RUNTIME_SHA256=hashlib.sha256(runtime).hexdigest()):
                PATCH.patch_archive(archive)
            with archive.open("rb") as stream:
                updated, payload = PATCH.read_header(stream)
                for filename, expected in (("server", PATCH.PR_WATCH_READ_AFTER),
                                           ("runtime", PATCH.PR_WATCH_GOAL_AFTER)):
                    entry = dict(PATCH.entries(updated))[filename]
                    stream.seek(payload + int(entry["offset"]))
                    self.assertIn(expected.encode(), stream.read(entry["size"]))

    def test_both_launch_paths_and_file_transfers_are_required(self):
        source = "#!/usr/bin/env node\n" + "\n".join(
            [*(before for before, _ in PATCH.REPLACEMENTS), PATCH.ANDROID_MISSING_SDK_BEFORE]
        )
        result = PATCH.patch_server(source)
        self.assertTrue(result.startswith("#!/usr/bin/env node\nimport "))
        for _, replacement in PATCH.REPLACEMENTS:
            self.assertIn(replacement, result)
        for before, _ in PATCH.REPLACEMENTS:
            with self.subTest(site=before):
                with self.assertRaises(ValueError):
                    PATCH.patch_server(source.replace(before, "changed upstream"))
                with self.assertRaises(ValueError):
                    PATCH.patch_server(source + before)

    def test_missing_sdk_reason_is_replaced_once_with_packaged_commands(self):
        source = "\n".join(
            [*(before for before, _ in PATCH.REPLACEMENTS), PATCH.ANDROID_MISSING_SDK_BEFORE]
        )
        result = PATCH.patch_server(source)
        self.assertNotIn(PATCH.ANDROID_MISSING_SDK_BEFORE, result)
        self.assertIn(PATCH.ANDROID_MISSING_SDK_AFTER, result)
        self.assertIn("t3code-android-diagnostics", result)
        self.assertIn("t3code-android-setup", result)
        with self.assertRaisesRegex(ValueError, "exactly one missing-SDK reason"):
            PATCH.patch_server(source.replace(PATCH.ANDROID_MISSING_SDK_BEFORE, "changed upstream"))
        with self.assertRaisesRegex(ValueError, "exactly one missing-SDK reason"):
            PATCH.patch_server(source + PATCH.ANDROID_MISSING_SDK_BEFORE)

    def test_archive_rewrite_preserves_other_files_and_updates_integrity(self):
        scratch = Path.home() / "agent-scratch/t3-flatpak/preview-unit-tests"
        scratch.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=scratch) as directory:
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
