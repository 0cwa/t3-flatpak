"""Exercise updater publication and retry behavior without network access."""

import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load(
    (ROOT / ".github/workflows/update-t3code-appimage.yml").read_text()
)
STEPS = {step.get("id", step.get("name")): step for step in WORKFLOW["jobs"]["update"]["steps"]}
PUBLISH = STEPS["publish"]["run"]
DISPATCH_STEP = STEPS["Reconcile downstream workflows for the default token"]
DISPATCH = DISPATCH_STEP["run"]

# State persists between invocations, just as already-created Actions runs do.
GH_STUB = r'''#!/usr/bin/env python3
import json
import os
from pathlib import Path
import sys

args = sys.argv[1:]
path = Path(os.environ["GH_TEST_STATE"])
state = json.loads(path.read_text())
state["calls"].append(args)
path.write_text(json.dumps(state))
if args[0] == "api":
    workflow = next(arg.split("/workflows/")[1].split("/")[0]
                    for arg in args if "/workflows/" in arg)
    assert args[args.index("--method") + 1] == "GET", args
    sha = next(arg.split("=", 1)[1] for arg in args if arg.startswith("head_sha="))
    if state.get("query_failure") == workflow:
        sys.exit(1)
    runs = [run for run in state["runs"]
            if run["workflow"] == workflow and run["head_sha"] == sha]
    print(json.dumps({"workflow_runs": runs}))
elif args[:2] == ["workflow", "run"]:
    workflow = args[2]
    if state.get("dispatch_failure") == workflow:
        del state["dispatch_failure"]
        path.write_text(json.dumps(state))
        sys.exit(1)
    ref = args[args.index("--ref") + 1]
    sha = os.environ["GH_TEST_RELEASE_SHA"] if workflow == "release.yml" else os.environ["GH_TEST_MAIN_SHA"]
    state["runs"].append({"id": len(state["runs"]) + 1, "workflow": workflow,
                          "head_sha": sha, "head_branch": ref, "event": "workflow_dispatch"})
    path.write_text(json.dumps(state))
else:
    raise AssertionError(args)
'''


class AppImageUpdateWorkflowTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.origin = self.root / "origin.git"
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "--bare", str(self.origin))
        self.git("init", "-b", "main")
        self.git("config", "user.name", "Updater Test")
        self.git("config", "user.email", "updater@example.invalid")
        self.manifest = self.repo / "com.t3tools.t3code.yml"
        self.manifest.write_text("version: old\n")
        self.git("add", self.manifest.name)
        self.git("commit", "-m", "initial")
        self.git("remote", "add", "origin", str(self.origin))
        self.git("push", "origin", "main")
        self.original_sha = self.git("rev-parse", "HEAD").strip()
        self.tag = "t3code-v1.2.3"
        self.output = self.root / "output"
        self.state_file = self.root / "gh-state.json"
        self.write_state({"calls": [], "runs": []})
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        stub = bin_dir / "gh"
        stub.write_text(GH_STUB)
        stub.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "VERSION": "v1.2.3",
            "GITHUB_OUTPUT": str(self.output),
            "GITHUB_REPOSITORY": "example/project",
            "CUSTOM_TOKEN": "",
            "RELEASE_TAG": self.tag,
            "PUBLISH_FLATPAK_REPO": "true",
            "GH_TEST_STATE": str(self.state_file),
            "GH_TEST_RELEASE_SHA": self.original_sha,
            "GH_TEST_MAIN_SHA": self.original_sha,
        }

    def git(self, *args):
        return subprocess.run(
            ["git", *args], cwd=self.repo, check=True, capture_output=True, text=True
        ).stdout

    def run_script(self, script, succeeds=True):
        result = subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", script],
            cwd=self.repo, env=self.env, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode == 0, succeeds, result.stdout + result.stderr)
        return result

    def state(self):
        return json.loads(self.state_file.read_text())

    def write_state(self, state):
        self.state_file.write_text(json.dumps(state))

    def create_tag(self):
        self.git("tag", "-a", self.tag, "-m", "release")
        self.git("push", "origin", self.tag)

    def dispatched(self):
        return [call for call in self.state()["calls"] if call[:2] == ["workflow", "run"]]

    def test_new_update_publishes_main_and_tag_together(self):
        self.manifest.write_text("version: new\n")
        self.run_script(PUBLISH)
        sha = self.git("rev-parse", "HEAD").strip()
        self.assertNotEqual(sha, self.original_sha)
        self.assertEqual(self.git("--git-dir", str(self.origin), "rev-parse", "refs/heads/main").strip(), sha)
        self.assertEqual(self.git("--git-dir", str(self.origin), "rev-parse", f"refs/tags/{self.tag}^{{commit}}").strip(), sha)
        self.assertIn(f"tag={self.tag}\n", self.output.read_text())

    def test_rejected_tag_does_not_advance_main(self):
        hook = self.origin / "hooks/pre-receive"
        hook.write_text('#!/bin/sh\nwhile read old new ref; do\n  case "$ref" in refs/tags/*) exit 1 ;; esac\ndone\n')
        hook.chmod(0o755)
        self.manifest.write_text("version: new\n")
        self.run_script(PUBLISH, succeeds=False)
        self.assertEqual(self.git("--git-dir", str(self.origin), "rev-parse", "refs/heads/main").strip(), self.original_sha)
        self.assertEqual(self.git("--git-dir", str(self.origin), "tag", "--list"), "")

    def test_missing_tag_and_existing_tag_both_reach_dispatch(self):
        self.assertNotIn("if", DISPATCH_STEP)
        self.run_script(PUBLISH)
        self.run_script(PUBLISH)
        self.assertEqual(self.output.read_text().count(f"tag={self.tag}\n"), 2)
        self.run_script(DISPATCH)
        self.assertEqual(len(self.dispatched()), 4)

    def test_release_uses_tag_and_pages_requires_explicit_opt_in(self):
        self.create_tag()
        for enabled in ("", "false", "true"):
            with self.subTest(enabled=enabled):
                self.write_state({"calls": [], "runs": []})
                self.env["PUBLISH_FLATPAK_REPO"] = enabled
                self.run_script(DISPATCH)
                calls = self.dispatched()
                self.assertEqual([call[2] for call in calls],
                                 ["release.yml", "build.yml", "security-baseline.yml"] + (["pages-flatpak-repo.yml"] if enabled == "true" else []))
                release = calls[0]
                self.assertEqual(release[release.index("--ref") + 1], self.tag)
                self.assertIn(f"tag={self.tag}", release)

    def test_rerun_recovers_only_missing_dispatches(self):
        self.create_tag()
        self.write_state({"calls": [], "runs": [], "dispatch_failure": "build.yml"})
        self.run_script(DISPATCH, succeeds=False)
        self.run_script(PUBLISH)  # Retry now finds an existing release tag.
        self.run_script(DISPATCH)
        self.assertEqual([run["workflow"] for run in self.state()["runs"]],
                         ["release.yml", "build.yml", "security-baseline.yml", "pages-flatpak-repo.yml"])
        self.assertEqual(sum(call[2] == "release.yml" for call in self.dispatched()), 1)
        count = len(self.dispatched())
        self.run_script(DISPATCH)
        self.assertEqual(len(self.dispatched()), count)

    def test_existing_push_runs_do_not_get_duplicate_dispatches(self):
        self.create_tag()
        self.write_state({"calls": [], "runs": [
            {"id": index, "workflow": workflow, "head_sha": self.original_sha,
             "head_branch": self.tag if workflow == "release.yml" else "main", "event": "push"}
            for index, workflow in enumerate(("release.yml", "build.yml", "security-baseline.yml", "pages-flatpak-repo.yml"), 1)
        ]})
        self.run_script(DISPATCH)
        self.assertEqual(self.dispatched(), [])

    def test_release_and_main_queries_use_their_own_commits(self):
        self.create_tag()
        (self.repo / "README.md").write_text("Later documentation change\n")
        self.git("add", "README.md")
        self.git("commit", "-m", "documentation")
        main_sha = self.git("rev-parse", "HEAD").strip()
        self.env["GH_TEST_MAIN_SHA"] = main_sha
        self.run_script(DISPATCH)
        queries = [call for call in self.state()["calls"] if call[0] == "api"]
        self.assertIn(f"head_sha={self.original_sha}", queries[0])
        for query in queries[1:]:
            self.assertIn(f"head_sha={main_sha}", query)

    def test_pr_and_other_ref_runs_do_not_suppress_dispatch(self):
        self.create_tag()
        self.write_state({"calls": [], "runs": [
            {"id": 1, "workflow": "build.yml", "head_sha": self.original_sha,
             "head_branch": "main", "event": "pull_request"},
            {"id": 2, "workflow": "release.yml", "head_sha": self.original_sha,
             "head_branch": "another-tag", "event": "push"},
        ]})
        self.run_script(DISPATCH)
        self.assertEqual(len(self.dispatched()), 4)

    def test_query_error_fails_without_dispatching(self):
        self.create_tag()
        self.write_state({"calls": [], "runs": [], "query_failure": "release.yml"})
        self.run_script(DISPATCH, succeeds=False)
        self.assertEqual(self.dispatched(), [])

    def test_custom_token_uses_push_triggers(self):
        self.env["CUSTOM_TOKEN"] = "test-token"
        self.run_script(DISPATCH)
        self.assertEqual(self.state()["calls"], [])


if __name__ == "__main__":
    unittest.main()
