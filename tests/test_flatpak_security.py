"""Regression cases for valid YAML that previously hid unsafe finish arguments."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


CHECKER = Path(__file__).resolve().parents[1] / "scripts/check-flatpak-security.py"
SAFE_LAUNCHER = '#!/bin/bash\nexec /app/bin/t3code "$@"\n'


class FlatpakSecurityTests(unittest.TestCase):
    def check_source(self, manifest, allowed, launcher=SAFE_LAUNCHER):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifest.yml").write_text(manifest)
            (root / "launcher").write_text(launcher)
            result = subprocess.run(
                [sys.executable, str(CHECKER), str(root / "manifest.yml"), str(root / "launcher")],
                capture_output=True, text=True, check=False,
            )
        self.assertEqual(result.returncode, 0 if allowed else 1, result.stdout + result.stderr)

    def test_yaml_scalar_forms_cannot_hide_host_access(self):
        for source in (
            'permission: &permission --filesystem=home\nfinish-args:\n  - *permission\n',
            'finish-args:\n  - "--filesystem=\\x68ome"\n',
            'finish-args:\n  - >-\n    --filesystem=home\n',
            'finish-args: ["--filesystem=home:ro"]\n',
            'defaults: &defaults {finish-args: ["--filesystem=home"]}\n<<: *defaults\n',
        ):
            with self.subTest(source=source):
                self.check_source(source, False)

    def test_wildcard_bus_names_and_split_arguments(self):
        for arguments in (
            '["--talk-name=org.freedesktop.*"]',
            '["--own-name=org.*"]',
            '["--talk-name", "org.freedesktop.Flatpak"]',
            '["--socket", "session-bus"]',
            '["--filesystem", "home:ro"]',
        ):
            with self.subTest(arguments=arguments):
                self.check_source(f"finish-args: {arguments}\n", False)

    def test_device_grants_reject_all_but_allow_narrow_devices(self):
        for argument in (
            '"--device=all"', '"--device", "all"',
            '"--device=usb"', '"--device", "input"',
        ):
            with self.subTest(argument=argument):
                self.check_source(f"finish-args: [{argument}]\n", False)
        self.check_source('finish-args: ["--device=kvm", "--device=dri"]\n', True)

    def test_normalized_credential_paths_and_parent_grants(self):
        for path in (
            "~/./.ssh:ro", "~/.ssh/key:ro", "~/.ssh/../.gnupg:ro",
            "~/.local:ro", "~/.config:ro", "xdg-config:ro",
            "/home/alice/.ssh:ro", "/var/home/alice/.gnupg:ro", "/root/.ssh:ro",
            "//home/alice/.ssh:ro", "~/../alice/.ssh:ro", "xdg-config/../.ssh:ro",
        ):
            with self.subTest(path=path):
                self.check_source(f'finish-args: ["--filesystem={path}"]\n', False)

    def test_host_pi_and_other_app_state_cannot_be_mounted(self):
        for path in (
            "~/.pi", "~/.pi:ro", "~/.pi/agent/auth.json:ro",
            "~/./.pi/agent/settings.json:ro", "home/.pi:ro",
            "home/.pi/agent/sessions:ro", "/home/alice/.pi:ro",
            "/var/home/alice/.pi:ro", "home/.ssh:ro",
            "~/.t3:ro", "~/.codex:ro", "~/.claude:ro",
        ):
            with self.subTest(path=path):
                self.check_source(f'finish-args: ["--filesystem={path}"]\n', False)

    def test_host_antigravity_state_cannot_be_mounted(self):
        for path in (
            "~/.gemini", "~/.gemini:ro", "~/.gemini/antigravity-cli/settings.json:ro",
            "~/./.gemini/config/skills:ro", "~/.gemini/../.gemini:ro",
            "home/.gemini:ro", "home/.gemini/antigravity-acp/acp_token.json:ro",
            "/home/alice/.gemini:ro", "/var/home/alice/.gemini:ro",
            "//home/alice/.gemini:ro", "/root/.gemini:ro", "/var/roothome/.gemini:ro",
            "~:ro", "home:ro", "/home/alice:ro", "/var/home/alice:ro",
        ):
            with self.subTest(path=path):
                self.check_source(f'finish-args: ["--filesystem={path}"]\n', False)
        self.check_source('finish-args: ["--filesystem=~/.gemini-public-project:ro"]\n', True)

    def test_invalid_or_ambiguous_manifests_fail_closed(self):
        for source in (
            "{}\n", "finish-args: false\n", "finish-args: [null]\n",
            "finish-args: [[--share=network]]\n", "finish-args: [\n",
            'finish-args: ["--share=network"]\nfinish-args: ["--filesystem=home"]\n',
            'finish-args: ["--filesystem"]\n',
            'finish-args: ["--filesystem="]\n',
        ):
            with self.subTest(source=source):
                self.check_source(source, False)

    def check_installed_permissions(self, grant, allowed, persistent=".ssh;.pi;.gemini;.local/bin;"):
        boundary = CHECKER.parents[1] / "tests/check-ssh-boundary.sh"
        code = boundary.read_text().split(
            'python3 - "$test_root/permissions" <<\'PY\'\n', 1
        )[1].split("\nPY\n", 1)[0]
        with tempfile.TemporaryDirectory() as directory:
            metadata = Path(directory) / "permissions"
            metadata.write_text(
                "[Context]\ndevices=dri;kvm;\n"
                f"persistent={persistent}\nfilesystems={grant};\n"
            )
            result = subprocess.run(
                [sys.executable, "-c", code, str(metadata)],
                capture_output=True, text=True, check=False,
            )
            self.assertEqual(result.returncode, 0 if allowed else 1, result.stdout + result.stderr)

    def test_installed_permissions_reject_credential_files_and_parents(self):
        for grant, allowed in (
            ("xdg-documents/projects:rw", True),
            ("~/.pi-public-project:ro", True),
            ("~/.gemini-public-project:ro", True),
            ("~/.pi/agent/auth.json:ro", False),
            ("~/./.pi/agent/auth.json:ro", False),
            ("home/.pi/agent/auth.json:ro", False),
            ("/home/alice/.pi/agent/auth.json:ro", False),
            ("//var/home/alice/.pi/agent/auth.json:ro", False),
            ("/root/.pi/agent/auth.json:ro", False),
            ("~/.gemini", False),
            ("~/.gemini/antigravity-acp/acp_token.json:ro", False),
            ("~/./.gemini/config/skills:ro", False),
            ("home/.gemini/antigravity-cli/settings.json:ro", False),
            ("/home/alice/.gemini:ro", False),
            ("//var/home/alice/.gemini:ro", False),
            ("/root/.gemini:ro", False),
            ("/var/roothome/.gemini:ro", False),
            ("/home/alice:ro", False),
            ("~/.ssh/key:ro", False),
            ("~/.codex/auth.json:ro", False),
            ("~/.local:ro", False),
            ("~:ro", False),
            ("/home:ro", False),
            ("host:ro", False),
            ("~/../alice/.pi:ro", False),
        ):
            with self.subTest(grant=grant):
                self.check_installed_permissions(grant, allowed)

    def test_installed_antigravity_state_requires_persistence(self):
        for persistent in (".ssh;.pi;.local/bin;", ".ssh;.pi;.gemini/config;.local/bin;"):
            with self.subTest(persistent=persistent):
                self.check_installed_permissions("", False, persistent)
        self.check_installed_permissions("", True)

    def test_safe_yaml_and_app_private_persistence_remain_allowed(self):
        self.check_source(
            'network: &network "--share=network"\n'
            'finish-args:\n'
            '  - *network\n'
            '  - --persist=.ssh\n'
            '  - --persist=.pi\n'
            '  - --persist=.gemini\n'
            '  - --persist=.codex\n'
            '  - --persist=.claude\n'
            '  - --persist=.t3\n'
            '  - --filesystem=xdg-documents/projects:rw\n'
            '  - --filesystem=~/.ssh-public-project:ro\n'
            '  - --talk-name=org.freedesktop.Notifications\n',
            True,
        )
        self.check_source("finish-args: []\n", True)

    def test_launcher_local_bin_requires_app_private_persistence(self):
        launcher = 'export PATH="$HOME/.local/bin:$PATH"\n'
        for manifest in (
            "finish-args: [--persist=.local/bin]\n",
            "finish-args: [--persist=./.local/bin/]\n",
            "finish-args: [--persist, .local/bin]\n",
        ):
            with self.subTest(manifest=manifest):
                self.check_source(manifest, True, launcher)
        for manifest in (
            "finish-args: [--persist=.local]\n",
            "finish-args: [--persist=.local/bin/tools]\n",
            'finish-args: [--persist=.local/bin, "--filesystem=~/.local/bin"]\n',
        ):
            with self.subTest(manifest=manifest):
                self.check_source(manifest, False, launcher)

    def test_known_launcher_bridges_remain_blocked(self):
        for launcher in (
            "#!/bin/bash\nflatpak-spawn --host bash\n",
            'export PATH="$HOME/.local/bin:$PATH"\n',
            'export PATH="${HOME}/.local/bin:$PATH"\n',
        ):
            with self.subTest(launcher=launcher):
                self.check_source("finish-args: []\n", False, launcher)


if __name__ == "__main__":
    unittest.main()
