"""Behavior checks for the optional Android Flatpak setup."""

import contextlib
import hashlib
import http.server
import importlib.util
import io
import multiprocessing
import os
from pathlib import Path
import stat
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import unittest
import zipfile
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
MODULE_PATH = ROOT / "scripts/android_setup.py"
SPEC = importlib.util.spec_from_file_location("android_setup", MODULE_PATH)
android_setup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(android_setup)


def _lock_worker(root, started, acquired):
    sys.stdout = open(os.devnull, "w")
    started.set()
    with android_setup._setup_lock(Path(root)):
        acquired.set()


class QuietHandler(http.server.BaseHTTPRequestHandler):
    files = {}
    requests = []

    def do_GET(self):
        self.requests.append(self.path)
        content = self.files.get(self.path)
        if content is None:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *_args):
        pass


class AndroidSetupTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="t3-android-tests-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    @staticmethod
    def make_java_tar(path, java_script=None, extra=None):
        java_script = java_script or b'#!/bin/sh\necho \'openjdk version "17.0.21"\' >&2\n'
        with tarfile.open(path, "w:gz") as archive:
            info = tarfile.TarInfo("jdk-fixture/bin/java")
            info.mode = 0o755
            info.size = len(java_script)
            archive.addfile(info, io.BytesIO(java_script))
            for name, kind, target in extra or []:
                member = tarfile.TarInfo(name)
                if kind == "symlink":
                    member.type = tarfile.SYMTYPE
                    member.linkname = target
                    archive.addfile(member)
                else:
                    content = target
                    member.size = len(content)
                    archive.addfile(member, io.BytesIO(content))
        return Path(path).read_bytes()

    @staticmethod
    def make_tools_zip(path, extra=None):
        with zipfile.ZipFile(path, "w") as archive:
            manager = zipfile.ZipInfo("cmdline-tools/bin/sdkmanager")
            manager.external_attr = (stat.S_IFREG | 0o755) << 16
            archive.writestr(manager, b"official sdkmanager fixture\n")
            source = zipfile.ZipInfo("cmdline-tools/source.properties")
            source.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(source, b"Pkg.Revision=1\n")
            for name, data, mode in extra or []:
                info = zipfile.ZipInfo(name)
                info.external_attr = mode << 16
                archive.writestr(info, data)
        return Path(path).read_bytes()

    @contextlib.contextmanager
    def local_downloads(self, java_bytes=None, tools_bytes=None, java_hash=None, tools_hash=None):
        java_bytes = java_bytes or self.make_java_tar(self.root / "java.tar.gz")
        tools_bytes = tools_bytes or self.make_tools_zip(self.root / "tools.zip")
        QuietHandler.files = {"/java": java_bytes, "/tools": tools_bytes}
        QuietHandler.requests = []
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        base_url = f"http://127.0.0.1:{server.server_port}"
        with mock.patch.multiple(
            android_setup,
            JAVA_URL=f"{base_url}/java",
            JAVA_SHA256=java_hash or hashlib.sha256(java_bytes).hexdigest(),
            TOOLS_URL=f"{base_url}/tools",
            TOOLS_SHA256=tools_hash or hashlib.sha256(tools_bytes).hexdigest(),
        ):
            yield

    @staticmethod
    def fake_sdkmanager(
        fail_install=False, grant_license=True, install_files=True,
        metadata_correct=True, image_payload=True,
    ):
        calls = []

        def run(command, env=None, input_text=None):
            calls.append((list(command), env.copy() if env else None, input_text))
            if command[-1] == "--licenses":
                if grant_license and input_text:
                    sdk = Path(command[1].split("=", 1)[1])
                    (sdk / "licenses").mkdir()
                    (sdk / "licenses/android-sdk-license").write_text("accepted fixture terms\n")
                return subprocess.CompletedProcess(command, 0)
            if "--install" in command:
                sdk = Path(command[1].split("=", 1)[1])
                if install_files:
                    for relative, package_id in (
                        ("platform-tools", "platform-tools"),
                        ("emulator", "emulator"),
                        ("system-images/android-35/google_apis/x86_64", "system-images;android-35;google_apis;x86_64"),
                    ):
                        directory = sdk / relative
                        directory.mkdir(parents=True, exist_ok=True)
                        reported_id = package_id if metadata_correct else "wrong-package"
                        (directory / "source.properties").write_text(
                            f"Pkg.Path={reported_id}\nPkg.Revision=1.0\n"
                        )
                    if image_payload:
                        image = sdk / "system-images/android-35/google_apis/x86_64/system.img"
                        image.write_bytes(b"fixture system image payload")
                    for relative in (
                        "platform-tools/adb", "emulator/emulator",
                        "cmdline-tools/latest/bin/avdmanager",
                    ):
                        executable = sdk / relative
                        executable.write_text("#!/bin/sh\nexit 0\n")
                        executable.chmod(0o755)
                if fail_install:
                    return subprocess.CompletedProcess(command, 1)
            return subprocess.CompletedProcess(command, 0)

        return run, calls

    def test_download_is_verified_before_archive_is_kept(self):
        data = b"local fixture"
        QuietHandler.files = {"/wrong": data}
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        destination = self.root / "bad-download"
        with self.assertRaisesRegex(android_setup.SetupError, "SHA-256"):
            android_setup.download_verified(
                f"http://127.0.0.1:{server.server_port}/wrong",
                "0" * 64, destination, 100,
            )
        self.assertFalse(destination.exists())

    def test_download_http_error_is_actionable_and_leaves_no_archive(self):
        QuietHandler.files = {}
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), QuietHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        destination = self.root / "missing-download"
        with self.assertRaisesRegex(android_setup.SetupError, "download failed with HTTP 404"):
            android_setup.download_verified(
                f"http://127.0.0.1:{server.server_port}/missing",
                "0" * 64, destination, 100,
            )
        self.assertFalse(destination.exists())

    def test_safe_extractors_reject_traversal_links_and_special_entries(self):
        tar_path = self.root / "bad.tar.gz"
        self.make_java_tar(
            tar_path,
            extra=[("../../escape", "file", b"bad")],
        )
        with self.assertRaisesRegex(android_setup.SetupError, "escapes"):
            android_setup.extract_tar_safely(tar_path, self.root / "tar-out")
        self.assertFalse((self.root.parent / "escape").exists())

        link_tar = self.root / "bad-link.tar.gz"
        self.make_java_tar(link_tar, extra=[("jdk-fixture/out", "symlink", "../../../outside")])
        with self.assertRaisesRegex(android_setup.SetupError, "link escapes"):
            android_setup.extract_tar_safely(link_tar, self.root / "link-out")

        zip_path = self.root / "bad.zip"
        self.make_tools_zip(zip_path, extra=[("../escape", b"bad", stat.S_IFREG | 0o644)])
        with self.assertRaisesRegex(android_setup.SetupError, "escapes"):
            android_setup.extract_zip_safely(zip_path, self.root / "zip-out")

        special_zip = self.root / "special.zip"
        self.make_tools_zip(
            special_zip,
            extra=[("cmdline-tools/bin/unexpected", b"bad", stat.S_IFLNK | 0o777)],
        )
        with self.assertRaisesRegex(android_setup.SetupError, "unsafe entry"):
            android_setup.extract_zip_safely(special_zip, self.root / "special-out")

    def test_explicit_setup_installs_pinned_tools_and_sdkmanager_packages(self):
        run, calls = self.fake_sdkmanager()
        root = self.root / "data/t3code/android"
        with self.local_downloads():
            with mock.patch.dict(os.environ, {
                "ANDROID_USER_HOME": str(root / "user"),
                "ANDROID_AVD_HOME": str(root / "user/avd"),
            }):
                android_setup.setup_android(
                    "system-images;android-35;google_apis;x86_64",
                    accept_licenses=True,
                    root=root,
                    runner=run,
                )
        active_sdk = root / "sdk/current"
        active_java = root / "java/current"
        self.assertTrue((active_sdk / "platform-tools/adb").is_file())
        self.assertTrue((active_sdk / "emulator/emulator").is_file())
        self.assertTrue((active_sdk / "cmdline-tools/latest/bin/avdmanager").is_file())
        self.assertTrue((active_sdk / "system-images/android-35/google_apis/x86_64").is_dir())
        self.assertEqual("17.0.21", (active_java / "bin/java").read_text().split('"')[1])
        install_call = next(call for call in calls if "--install" in call[0])
        self.assertEqual(
            ["platform-tools", "emulator", "system-images;android-35;google_apis;x86_64"],
            install_call[0][3:],
        )
        self.assertTrue(all(call[2] and call[2].startswith("y\n") for call in calls))
        self.assertEqual(str(root / "user"), calls[0][1]["ANDROID_USER_HOME"])
        self.assertEqual(str(root / "user/avd"), calls[0][1]["ANDROID_AVD_HOME"])
        self.assertTrue((root / "user/avd").is_dir())
        self.assertTrue((active_sdk / "licenses/android-sdk-license").is_file())
        self.assertIn("Pkg.Path=emulator", (active_sdk / "emulator/source.properties").read_text())
        self.assertIn(
            "Pkg.Path=system-images;android-35;google_apis;x86_64",
            (active_sdk / "system-images/android-35/google_apis/x86_64/source.properties").read_text(),
        )
        self.assertEqual([], list(root.glob(".setup-*")))

    def test_license_review_without_consent_requires_a_terminal_before_download(self):
        with self.local_downloads():
            with mock.patch.object(android_setup.sys.stdin, "isatty", return_value=False):
                with self.assertRaisesRegex(android_setup.SetupError, "needs a terminal"):
                    android_setup.setup_android(
                        "system-images;android-35;google_apis;x86_64",
                        root=self.root / "data/t3code/android",
                    )
        self.assertFalse((self.root / "data/t3code/android/sdk/current").exists())

    def test_interactive_refusal_stops_before_download(self):
        with self.local_downloads():
            with mock.patch.object(android_setup.sys.stdin, "isatty", return_value=True):
                with mock.patch("builtins.input", return_value="no"):
                    with self.assertRaisesRegex(android_setup.SetupError, "licenses were not accepted"):
                        android_setup.setup_android(
                            "system-images;android-35;google_apis;x86_64",
                            root=self.root / "data/t3code/android",
                        )
        self.assertFalse((self.root / "data/t3code/android/sdk/current").exists())

    def test_sdkmanager_exit_zero_without_license_or_package_evidence_fails(self):
        root = self.root / "data/t3code/android"
        old_sdk = root / "sdk/versions/known-good"
        old_java = root / "java/versions/known-good"
        old_sdk.mkdir(parents=True)
        old_java.mkdir(parents=True)
        (old_sdk / "marker").write_text("old SDK")
        (old_java / "marker").write_text("old Java")
        (root / "sdk/current").symlink_to("versions/known-good")
        (root / "java/current").symlink_to("versions/known-good")

        run, calls = self.fake_sdkmanager(grant_license=False)
        with self.local_downloads():
            with self.assertRaisesRegex(android_setup.SetupError, "did not record acceptance"):
                android_setup.setup_android(
                    "system-images;android-35;google_apis;x86_64", True, root, runner=run
                )
        self.assertFalse(any("--install" in call[0] for call in calls))
        self.assertEqual("old SDK", (root / "sdk/current/marker").read_text())
        self.assertEqual("old Java", (root / "java/current/marker").read_text())

        run, _ = self.fake_sdkmanager(install_files=False)
        with self.local_downloads():
            with self.assertRaisesRegex(android_setup.SetupError, "without executable tools"):
                android_setup.setup_android(
                    "system-images;android-35;google_apis;x86_64", True, root, runner=run
                )
        self.assertEqual("old SDK", (root / "sdk/current/marker").read_text())

        run, _ = self.fake_sdkmanager(metadata_correct=False)
        with self.local_downloads():
            with self.assertRaisesRegex(android_setup.SetupError, "metadata is incomplete"):
                android_setup.setup_android(
                    "system-images;android-35;google_apis;x86_64", True, root, runner=run
                )
        self.assertEqual("old SDK", (root / "sdk/current/marker").read_text())

        run, _ = self.fake_sdkmanager(image_payload=False)
        with self.local_downloads():
            with self.assertRaisesRegex(android_setup.SetupError, "system image is incomplete"):
                android_setup.setup_android(
                    "system-images;android-35;google_apis;x86_64", True, root, runner=run
                )
        self.assertEqual("old SDK", (root / "sdk/current/marker").read_text())

    def test_external_sdk_override_is_never_modified_by_setup(self):
        external_sdk = self.root / "host-sdk"
        external_sdk.mkdir()
        (external_sdk / "marker").write_text("read-only fixture")
        private = self.root / "data/t3code/android"
        with mock.patch.dict(os.environ, {"ANDROID_HOME": str(external_sdk)}):
            with self.local_downloads():
                with self.assertRaisesRegex(android_setup.SetupError, "will not modify"):
                    android_setup.setup_android(
                        "system-images;android-35;google_apis;x86_64", True, private
                    )
        self.assertEqual("read-only fixture", (external_sdk / "marker").read_text())
        self.assertFalse((private / "sdk/current").exists())

    def test_private_java_install_keeps_explicit_read_only_sdk_untouched(self):
        external_sdk = self.root / "host-sdk"
        external_sdk.mkdir()
        marker = external_sdk / "marker"
        marker.write_text("read-only fixture")
        private = self.root / "data/t3code/android"
        with mock.patch.dict(os.environ, {"ANDROID_HOME": str(external_sdk)}, clear=True):
            with self.local_downloads():
                android_setup.install_private_java(root=private)
        java = private / "java/current/bin/java"
        self.assertTrue(java.is_file() and os.access(java, os.X_OK))
        self.assertEqual("read-only fixture", marker.read_text())
        self.assertFalse((private / "sdk").exists())
        self.assertEqual(["/java"], QuietHandler.requests)
        captured = self.capture_launcher(
            self.root / "data", ANDROID_HOME=str(external_sdk)
        )
        self.assertEqual(str(external_sdk), captured["ANDROID_HOME"])
        self.assertEqual(str(private / "java/current"), captured["JAVA_HOME"])

    def test_unsupported_image_abi_is_rejected_before_setup(self):
        with self.assertRaisesRegex(android_setup.SetupError, "x86_64"):
            android_setup.setup_android(
                "system-images;android-35;google_apis;arm64-v8a",
                accept_licenses=True,
                root=self.root / "data/t3code/android",
            )
        self.assertFalse((self.root / "data/t3code/android").exists())

    def test_checksum_and_sdkmanager_failures_keep_active_generations(self):
        root = self.root / "data/t3code/android"
        old_sdk = root / "sdk/versions/known-good"
        old_java = root / "java/versions/known-good"
        old_sdk.mkdir(parents=True)
        old_java.mkdir(parents=True)
        (old_sdk / "marker").write_text("old SDK")
        (old_java / "marker").write_text("old Java")
        (root / "sdk/current").symlink_to("versions/known-good")
        (root / "java/current").symlink_to("versions/known-good")

        java_bytes = self.make_java_tar(self.root / "java.tar.gz")
        tools_bytes = self.make_tools_zip(self.root / "tools.zip")
        run, _ = self.fake_sdkmanager(fail_install=True)
        with self.local_downloads(java_bytes, tools_bytes, java_hash="0" * 64):
            with self.assertRaisesRegex(android_setup.SetupError, "SHA-256"):
                android_setup.setup_android(
                    "system-images;android-35;google_apis;x86_64", True, root, runner=run
                )
        with self.local_downloads(java_bytes, tools_bytes):
            with self.assertRaisesRegex(android_setup.SetupError, "sdkmanager failed"):
                android_setup.setup_android(
                    "system-images;android-35;google_apis;x86_64", True, root, runner=run
                )
        self.assertEqual("old SDK", (root / "sdk/current/marker").read_text())
        self.assertEqual("old Java", (root / "java/current/marker").read_text())
        self.assertEqual([], list(root.glob(".setup-*")))

    def test_interrupted_sdkmanager_run_cleans_stage_and_preserves_current(self):
        root = self.root / "data/t3code/android"
        old_sdk = root / "sdk/versions/known-good"
        old_java = root / "java/versions/known-good"
        old_sdk.mkdir(parents=True)
        old_java.mkdir(parents=True)
        (old_sdk / "marker").write_text("old SDK")
        (old_java / "marker").write_text("old Java")
        (root / "sdk/current").symlink_to("versions/known-good")
        (root / "java/current").symlink_to("versions/known-good")

        def interrupted(command, env=None, input_text=None):
            if command[-1] == "--licenses":
                sdk = Path(command[1].split("=", 1)[1])
                (sdk / "licenses").mkdir()
                (sdk / "licenses/android-sdk-license").write_text("accepted fixture terms\n")
                return subprocess.CompletedProcess(command, 0)
            raise KeyboardInterrupt

        with self.local_downloads():
            with self.assertRaises(KeyboardInterrupt):
                android_setup.setup_android(
                    "system-images;android-35;google_apis;x86_64", True, root, runner=interrupted
                )
        self.assertEqual("old SDK", (root / "sdk/current/marker").read_text())
        self.assertEqual("old Java", (root / "java/current/marker").read_text())
        self.assertEqual([], list(root.glob(".setup-*")))

    def test_setup_lock_serializes_separate_processes(self):
        context = multiprocessing.get_context("fork")
        started = context.Event()
        acquired = context.Event()
        root = self.root / "lock-test"
        with android_setup._setup_lock(root):
            sys.stdout.flush()
            child = context.Process(target=_lock_worker, args=(str(root), started, acquired))
            child.start()
            self.assertTrue(started.wait(2))
            self.assertFalse(acquired.wait(0.2))
        child.join(3)
        self.assertEqual(0, child.exitcode)
        self.assertTrue(acquired.is_set())

    def test_launcher_keeps_private_state_and_honors_explicit_overrides(self):
        data = self.root / "data"
        private = data / "t3code/android"
        sdk = private / "sdk/versions/active"
        java = private / "java/versions/active"
        sdk.mkdir(parents=True)
        java.mkdir(parents=True)
        java_bin = java / "bin/java"
        java_bin.parent.mkdir()
        java_bin.write_text("#!/bin/sh\nexit 0\n")
        java_bin.chmod(0o755)
        (private / "sdk/current").symlink_to("versions/active")
        (private / "java/current").symlink_to("versions/active")
        captured = self.capture_launcher(data)
        self.assertEqual(str(private / "sdk/current"), captured["ANDROID_HOME"])
        self.assertEqual(str(private / "sdk/current"), captured["ANDROID_SDK_ROOT"])
        self.assertEqual(str(private / "java/current"), captured["JAVA_HOME"])
        self.assertEqual(str(private / "user"), captured["ANDROID_USER_HOME"])
        self.assertEqual(str(private / "user/avd"), captured["ANDROID_AVD_HOME"])

        explicit_sdk = self.root / "mounted-sdk"
        explicit_java = self.root / "chosen-java"
        explicit_user = self.root / "android-preferences"
        explicit_avd = self.root / "android-avds"
        for directory in (explicit_sdk, explicit_java, explicit_user, explicit_avd):
            directory.mkdir()
        captured = self.capture_launcher(
            data,
            ANDROID_SDK_ROOT=str(explicit_sdk),
            JAVA_HOME=str(explicit_java),
            ANDROID_USER_HOME=str(explicit_user),
            ANDROID_AVD_HOME=str(explicit_avd),
        )
        self.assertEqual(str(explicit_sdk), captured["ANDROID_HOME"])
        self.assertEqual(str(explicit_sdk), captured["ANDROID_SDK_ROOT"])
        self.assertEqual(str(explicit_java), captured["JAVA_HOME"])
        self.assertEqual(str(explicit_user), captured["ANDROID_USER_HOME"])
        self.assertEqual(str(explicit_avd), captured["ANDROID_AVD_HOME"])

    def capture_launcher(self, data, **overrides):
        output = self.root / f"launcher-env-{time.time_ns()}"
        home = self.root / "home"
        runtime = self.root / "runtime"
        runtime.mkdir(exist_ok=True)
        env = {
            "HOME": str(home),
            "XDG_DATA_HOME": str(data),
            "XDG_CACHE_HOME": str(self.root / "cache"),
            "XDG_RUNTIME_DIR": str(runtime),
            "T3_TEST_ENV_OUT": str(output),
            "PATH": "/usr/bin:/bin",
            "FLATPAK_ID": "com.t3tools.t3code",
            "T3_LAUNCHER": str(ROOT / "startt3code"),
        }
        env.update(overrides)
        script = (
            'zypak-wrapper() { printf \'%s\\n\' "$ANDROID_HOME" "$ANDROID_SDK_ROOT" '
            '"$JAVA_HOME" "$ANDROID_USER_HOME" "$ANDROID_AVD_HOME" > "$T3_TEST_ENV_OUT"; }; '
            'source "$T3_LAUNCHER"'
        )
        result = subprocess.run(["bash", "-c", script], env=env, capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        return dict(zip(
            ("ANDROID_HOME", "ANDROID_SDK_ROOT", "JAVA_HOME", "ANDROID_USER_HOME", "ANDROID_AVD_HOME"),
            output.read_text().splitlines(),
        ))

    def test_diagnostics_report_sdk_java_kvm_and_install_command(self):
        output = io.StringIO()
        with mock.patch.object(android_setup.os, "access", return_value=False):
            result = android_setup.diagnose(
                {"XDG_DATA_HOME": str(self.root / "data"), "PATH": ""}, output
            )
        report = output.getvalue()
        self.assertEqual(1, result)
        self.assertIn("Android SDK (app-private)", report)
        self.assertIn("platform-tools/adb: missing", report)
        self.assertIn("Java: unavailable", report)
        self.assertIn("KVM:", report)
        self.assertIn("t3code-android-setup", report)
        self.assertIn("install-java", report)

    def test_diagnostics_keep_reporting_when_java_loader_is_missing(self):
        java_home = self.root / "broken-java"
        (java_home / "bin").mkdir(parents=True)
        java = java_home / "bin/java"
        java.write_text("#!/missing/loader\n")
        java.chmod(0o755)
        output = io.StringIO()
        result = android_setup.diagnose(
            {
                "XDG_DATA_HOME": str(self.root / "data"),
                "JAVA_HOME": str(java_home),
                "PATH": "",
            },
            output,
        )
        report = output.getvalue()
        self.assertEqual(1, result)
        self.assertIn("Java: ", report)
        self.assertIn("failed to start", report)
        self.assertIn("Setup command:", report)


if __name__ == "__main__":
    unittest.main()
