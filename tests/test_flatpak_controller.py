import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
CONTROLLER = ROOT / "scripts/t3-flatpak-ctl"
APP = "com.t3tools.t3code"
REF = f"app/{APP}/x86_64/master"
TOOLS = f"{APP}.Tools"


class ControllerTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.responses_path = self.root / "responses.json"
        shim = self.root / "flatpak"
        shim.write_text(
            f"#!{sys.executable}\n"
            "import json, os, pathlib, sys\n"
            "answers = json.loads(pathlib.Path(os.environ['RESPONSES']).read_text())\n"
            "sys.stdout.write(answers[' '.join(sys.argv[1:])])\n"
        )
        shim.chmod(0o755)
        self.home = self.root / "home"
        self.home.mkdir()
        self.env = {
            **os.environ,
            "PATH": f"{self.root}:{os.environ['PATH']}",
            "RESPONSES": str(self.responses_path),
            "XDG_RUNTIME_DIR": str(self.root),
            "HOME": str(self.home),
        }
        self.app_root = self.root / REF
        self.responses = {
            f"info --user --show-ref {APP}": REF,
            f"info --user --show-metadata {REF}":
                f"[Extension {TOOLS}]\nversion=26.08\n[Context]\nsockets=wayland;\n",
            f"info --user --show-commit {REF}": "new-commit",
            f"info --user --show-location {REF}": str(self.app_root / "new-commit"),
            f"info --user --show-permissions {REF}":
                "[Context]\nfilesystems=/projects;!/projects/private;\n"
                "[Session Bus Policy]\norg.freedesktop.Flatpak=talk\n"
                "[Environment]\nAPI_TOKEN=never-print-this\n",
            "override --user --show": "[Context]\nfilesystems=/projects;\n",
            f"override --user --show {APP}":
                "[Session Bus Policy]\norg.freedesktop.Flatpak=talk\n",
            "override --system --show": "",
            f"override --system --show {APP}": "",
            "list --runtime --columns=ref:f,installation:f,origin:f":
                f"{TOOLS}.Good/x86_64/26.08\tuser\ttools\n"
                f"{TOOLS}.Old/x86_64/25.08\tsystem\ttools\n",
            "ps --columns=instance:f,application:f": f"10\t{APP}\n20\t{APP}\n30\t{APP}\n",
        }
        for ident, commit, sandbox in (("10", "old-commit", ""), ("20", "new-commit", "sandbox=true\n")):
            info = self.root / ".flatpak" / ident / "info"
            info.parent.mkdir(parents=True)
            info.write_text(
                f"[Instance]\napp-path={self.app_root / commit / 'files'}\n"
                f"app-commit={commit}\n{sandbox}app-extensions={TOOLS}.Good=abc;\n"
                "[Context]\nfilesystems=/running;\n[Environment]\nAPI_TOKEN=another-secret\n"
            )

    def run_ctl(self, *args, succeeds=True):
        self.responses_path.write_text(json.dumps(self.responses))
        result = subprocess.run(
            [sys.executable, CONTROLLER, *args],
            capture_output=True, text=True, env=self.env,
        )
        self.assertEqual(result.returncode == 0, succeeds, result.stderr)
        return result

    def test_status_separates_layers_and_running_state(self):
        output = self.run_ctl("status", "--json").stdout
        self.assertNotIn("API_TOKEN", output)
        self.assertNotIn("never-print-this", output)
        self.assertNotIn("another-secret", output)
        data = json.loads(output)
        layers = data["permission_layers"]
        self.assertEqual(layers["manifest"]["sockets"], ["wayland"])
        self.assertEqual(layers["user global override"]["filesystems"], ["/projects"])
        self.assertEqual(
            layers["user app override"]["host_portal_rules"],
            {"org.freedesktop.Flatpak": "talk"},
        )
        self.assertIn("!/projects/private", data["next_launch_defaults"]["filesystems"])
        app, child = data["running_instances"]
        self.assertTrue(app["restart_for_installed_build"])
        self.assertEqual(app["permissions"]["filesystems"], ["/running"])
        self.assertEqual(app["mounted_tools"], [f"{TOOLS}.Good=abc"])
        self.assertEqual(child["kind"], "restricted child")
        self.assertIsNone(child["restart_for_installed_build"])
        self.assertTrue(data["installed_tools"][0]["matches_arch_and_branch"])
        self.assertFalse(data["installed_tools"][1]["matches_arch_and_branch"])

    def test_human_report_identifies_host_portal_and_restart(self):
        output = self.run_ctl("status").stdout
        self.assertTrue(output.startswith("Host portal rules for next launch:"))
        self.assertIn("Restart needed for installed build: yes", output)
        self.assertNotIn("another-secret", output)

    def test_path_uses_flatpak_for_each_spelling_and_keeps_denial(self):
        destination = self.root / "projects/private"
        destination.mkdir(parents=True)
        alias = self.root / "alias"
        alias.symlink_to(destination)
        self.responses[f"info --user --file-access={alias} {REF}"] = "read-only\n"
        self.responses[f"info --user --file-access={destination} {REF}"] = "hidden\n"
        output = self.run_ctl("check-path", alias).stdout
        self.assertIn(f"{alias}: read-only", output)
        self.assertIn(f"{destination}: hidden", output)

    def test_project_plan_quotes_the_path_and_never_applies_it(self):
        project = self.home / "project with spaces"
        project.mkdir()
        command, _ = self.run_ctl("plan", "project", project).stdout.splitlines()
        self.assertEqual(shlex.split(command), [
            "flatpak", "override", "--user", f"--filesystem={project}:ro", APP,
        ])
        self.run_ctl("plan", "project", self.home, succeeds=False)

    def test_extension_plan_uses_the_declared_tools_branch(self):
        output = self.run_ctl("plan", "extension", "tools", f"{TOOLS}.Good").stdout
        self.assertIn(f"runtime/{TOOLS}.Good/x86_64/26.08", output)
        self.assertNotIn("/master", output)
        self.run_ctl("plan", "extension", "tools", "org.example.Unrelated", succeeds=False)


if __name__ == "__main__":
    unittest.main()
