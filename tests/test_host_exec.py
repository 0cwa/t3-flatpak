import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "host-exec"
APP_ID = "com.t3tools.t3code"


class HostExecTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.physical = self.root / "var" / "home" / "test user"
        self.physical.mkdir(parents=True)
        (self.root / "home").symlink_to(self.physical.parent)
        self.home = self.root / "home" / self.physical.name
        self.app_home = self.home / ".var" / "app" / APP_ID
        self.data = self.app_home / "data"
        self.data.mkdir(parents=True)
        self.worktree = self.app_home / ".t3" / "worktrees" / "calendar repo"
        self.worktree.mkdir(parents=True)
        (self.home / ".t3").symlink_to(self.worktree.parent.parent)
        self.env = {
            **os.environ,
            "HOME": str(self.home),
            "FLATPAK_ID": APP_ID,
            "XDG_DATA_HOME": str(self.data),
        }
        # Use the production shim, with only the portal binary replaced.
        portal = self.root / "portal"
        portal.write_text(
            "#!/usr/bin/env python3\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n"
        )
        portal.chmod(0o755)
        self.shim = self.root / "host-exec"
        self.shim.write_text(
            (SOURCE / "host-exec").read_text().replace("/usr/bin/flatpak-spawn", str(portal))
        )
        self.destination = self.data / "npm-global" / "bin" / "host-exec"

    def run_shim(self, cwd, *args):
        return subprocess.run(
            ["bash", self.shim, *args],
            cwd=cwd,
            env={**self.env, "PWD": str(cwd)},
            capture_output=True,
            text=True,
            check=True,
        )

    def install(self, *args, env=None, installer=None):
        return subprocess.run(
            ["bash", installer or SOURCE / "t3code-install-host-exec", *args],
            env=self.env if env is None else env,
            capture_output=True,
            text=True,
        )

    def test_maps_both_home_aliases_and_preserves_arguments(self):
        for home in (self.home, self.physical):
            with self.subTest(home=home):
                cwd = home / ".t3" / "worktrees" / self.worktree.name
                args = ["printf", "%s\n", "with spaces", "$(echo should-stay-literal)", ""]
                forwarded = json.loads(self.run_shim(cwd, *args).stdout)
                self.assertEqual(forwarded[:3], [
                    "--host", "--watch-bus", f"--directory={self.worktree}"
                ])
                self.assertEqual(forwarded[3:5], ["/bin/bash", "-lc"])
                self.assertEqual(forwarded[6:], ["t3-host-exec", *args])

    def test_preserves_other_paths_and_nonpersisted_override(self):
        project = self.physical / "Dev" / "calendar"
        project.mkdir(parents=True)
        override = self.physical / ".codex" / "project"
        override.mkdir(parents=True)
        (self.app_home / ".codex").mkdir()
        for cwd in (project, override):
            with self.subTest(cwd=cwd):
                forwarded = json.loads(self.run_shim(cwd, "pwd").stdout)
                self.assertEqual(forwarded[2], f"--directory={cwd}")

    def test_installs_through_packaged_launcher_link(self):
        launcher = self.root / "t3code-install-host-exec"
        launcher.symlink_to(SOURCE / launcher.name)
        self.assertFalse(self.destination.exists())
        installed = self.install(installer=launcher)
        self.assertEqual(installed.returncode, 0, installed.stderr)
        self.assertEqual(self.destination.read_bytes(), (SOURCE / "host-exec").read_bytes())
        self.assertTrue(os.access(self.destination, os.X_OK))

    def test_replacement_needs_explicit_flag(self):
        self.destination.parent.mkdir(parents=True)
        self.destination.write_text("local shim\n")
        refused = self.install()
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("--replace", refused.stderr)
        self.assertEqual(self.destination.read_text(), "local shim\n")
        replaced = self.install("--replace")
        self.assertEqual(replaced.returncode, 0, replaced.stderr)
        self.assertEqual(self.destination.read_bytes(), (SOURCE / "host-exec").read_bytes())

    def test_rejects_other_app_data_and_existing_symlink(self):
        wrong_app = self.install(env={**self.env, "FLATPAK_ID": "org.example.App"})
        other_data = self.root / "other-data"
        other_data.mkdir()
        wrong_data = self.install(env={**self.env, "XDG_DATA_HOME": str(other_data)})
        self.assertNotEqual(wrong_app.returncode, 0)
        self.assertNotEqual(wrong_data.returncode, 0)
        self.assertFalse(self.destination.exists())

        self.destination.parent.mkdir(parents=True)
        other_shim = self.root / "unrelated-shim"
        other_shim.write_text("untouched\n")
        self.destination.symlink_to(other_shim)
        refused = self.install("--replace")
        self.assertNotEqual(refused.returncode, 0)
        self.assertTrue(self.destination.is_symlink())
        self.assertEqual(other_shim.read_text(), "untouched\n")


if __name__ == "__main__":
    unittest.main()
