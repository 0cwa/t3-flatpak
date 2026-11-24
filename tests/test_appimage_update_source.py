"""Check cheap hourly release discovery without weakening download validation."""

import hashlib
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
API = "https://api.github.com/repos/pingdotgg/t3code/releases/latest"
NAME = "T3-Code-x86_64.AppImage"
PAYLOAD = b"synthetic AppImage for the update check\n"
DIGEST = hashlib.sha256(PAYLOAD).hexdigest()


class AppImageDiscoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.manifest = self.root / "com.t3tools.t3code.yml"
        self.calls = self.root / "calls.jsonl"
        self.output = self.root / "output"

    def source(self, version="v0.1.11"):
        return (
            "      - type: file\n"
            f"        url: https://github.com/pingdotgg/t3code/releases/download/{version}/{NAME}\n"
            "        dest-filename: T3-Code.AppImage\n"
            f"        sha256: {DIGEST}\n"
            "        only-arches: [x86_64]\n"
        )

    def run_updater(self, version="v0.1.11", digest=True, **release_flags):
        asset = {
            "name": NAME,
            "browser_download_url": f"https://github.com/pingdotgg/t3code/releases/download/{version}/{NAME}",
        }
        if digest:
            asset["digest"] = f"sha256:{DIGEST}"
        fixture = self.root / "fixture.json"
        fixture.write_text(json.dumps({
            "tag_name": version, "assets": [asset], **release_flags,
        }))
        preload = self.root / "fetch.cjs"
        preload.write_text(r"""
const fs = require("node:fs");
const release = JSON.parse(fs.readFileSync(process.env.T3_TEST_FIXTURE, "utf8"));
global.fetch = async (url) => {
  fs.appendFileSync(process.env.T3_TEST_CALLS, JSON.stringify(url) + "\n");
  if (url === "https://api.github.com/repos/pingdotgg/t3code/releases/latest")
    return new Response(JSON.stringify(release));
  if (url === release.assets[0].browser_download_url)
    return new Response("synthetic AppImage for the update check\n");
  throw new Error("Unexpected request: " + url);
};
""")
        result = subprocess.run(
            ["node", "--require", str(preload), str(ROOT / "scripts/update-t3code-appimage.js")],
            cwd=self.root,
            env={**os.environ, "GITHUB_TOKEN": "", "GITHUB_OUTPUT": str(self.output),
                 "T3_TEST_FIXTURE": str(fixture), "T3_TEST_CALLS": str(self.calls)},
            text=True, capture_output=True,
        )
        calls = [json.loads(line) for line in self.calls.read_text().splitlines()]
        return result, calls

    def test_unchanged_verified_release_only_fetches_metadata(self):
        source = self.source()
        self.manifest.write_text(source)
        before = self.manifest.stat().st_mtime_ns
        result, calls = self.run_updater()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(calls, [API])
        self.assertEqual(self.manifest.read_text(), source)
        self.assertEqual(self.manifest.stat().st_mtime_ns, before)
        self.assertEqual(self.output.read_text(), "version=v0.1.11\n")

    def test_new_release_with_same_bytes_still_downloads_and_verifies(self):
        self.manifest.write_text(self.source())
        result, calls = self.run_updater(version="v0.1.12")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 2)
        self.assertEqual(self.manifest.read_text(), self.source("v0.1.12"))

    def test_release_without_published_digest_still_downloads(self):
        self.manifest.write_text(self.source())
        result, calls = self.run_updater(digest=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(calls), 2)

    def test_duplicate_source_is_rejected_without_downloading_or_modifying(self):
        source = self.source() * 2
        self.manifest.write_text(source)
        result, calls = self.run_updater()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Expected exactly one", result.stderr)
        self.assertEqual(calls, [API])
        self.assertEqual(self.manifest.read_text(), source)

    def test_nonstable_release_is_rejected_without_downloading_or_modifying(self):
        source = self.source()
        for flag in ("draft", "prerelease"):
            with self.subTest(flag=flag):
                self.manifest.write_text(source)
                self.calls.unlink(missing_ok=True)
                result, calls = self.run_updater(**{flag: True})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("published stable", result.stderr)
                self.assertEqual(calls, [API])
                self.assertEqual(self.manifest.read_text(), source)


if __name__ == "__main__":
    unittest.main()
