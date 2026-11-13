"""Guard the two launch paths and asar offset/integrity rewrites."""

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


class PreviewPatchTests(unittest.TestCase):
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
