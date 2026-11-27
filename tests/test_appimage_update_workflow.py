"""Exercise AppImage repinning, release verification, and publication offline."""

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = yaml.safe_load(
    (ROOT / ".github/workflows/update-t3code-appimage.yml").read_text()
)


def steps(job):
    return {step.get("id", step.get("name")): step for step in job["steps"]}


STEPS = steps(WORKFLOW["jobs"]["publish"])
PREPARE = steps(WORKFLOW["jobs"]["update"])["prepare"]["run"]
APPLY_CANDIDATE = steps(WORKFLOW["jobs"]["validate"])["candidate"]["run"]
VERIFY_CANDIDATE = steps(WORKFLOW["jobs"]["validate"])["Verify candidate stayed unchanged"]["run"]
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

FETCH_STUB = r'''
const fs = require("node:fs");
const fixture = JSON.parse(fs.readFileSync(process.env.T3_TEST_RELEASE_FIXTURE, "utf8"));
global.fetch = async (url) => {
  if (url === "https://api.github.com/repos/pingdotgg/t3code/releases/latest") {
    return new Response(JSON.stringify(fixture.release));
  }
  if (url === fixture.downloadUrl) return new Response(fixture.payload);
  throw new Error(`Unexpected network request: ${url}`);
};
'''


class AppImageSourceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.manifest = self.root / "com.t3tools.t3code.yml"
        self.output = self.root / "output"
        self.payload = "synthetic AppImage bytes for checksum verification\n"
        self.digest = hashlib.sha256(self.payload.encode()).hexdigest()
        self.tag = "v0.1.11"
        self.env = {
            **os.environ,
            "GITHUB_TOKEN": "",
            "GITHUB_OUTPUT": str(self.output),
            "GITHUB_REPOSITORY": "example/project",
            "GITHUB_WORKFLOW": "Release test",
            "GITHUB_SERVER_URL": "https://github.com",
            "GITHUB_RUN_ID": "1",
            "GITHUB_REF": "refs/tags/t3code-v0.1.11",
            "GITHUB_SHA": "a" * 40,
            "RELEASE_SOURCE_SHA": "a" * 40,
        }

    def source(self, name, tag="v0.1.9", digest="a" * 64):
        return (
            "      - type: file\n"
            f"        url: https://github.com/pingdotgg/t3code/releases/download/{tag}/{name}\n"
            "        dest-filename: T3-Code.AppImage\n"
            f"        sha256: {digest}\n"
            "        only-arches: [x86_64]\n"
        )

    def asset(self, name, **overrides):
        return {
            "name": name,
            "browser_download_url": f"https://github.com/pingdotgg/t3code/releases/download/{self.tag}/{name}",
            "digest": f"sha256:{self.digest}",
            **overrides,
        }

    def run_updater(self, assets, succeeds=True, tag=None):
        fixture = self.root / "release.json"
        fixture.write_text(json.dumps({
            "release": {"tag_name": tag or self.tag, "assets": assets},
            "downloadUrl": self.asset(assets[0]["name"])["browser_download_url"] if assets else "",
            "payload": self.payload,
        }))
        preload = self.root / "fetch.cjs"
        preload.write_text(FETCH_STUB)
        result = subprocess.run(
            ["node", "--require", str(preload), str(ROOT / "scripts/update-t3code-appimage.js")],
            cwd=self.root,
            env={**self.env, "T3_TEST_RELEASE_FIXTURE": str(fixture)},
            capture_output=True, text=True,
        )
        self.assertEqual(result.returncode == 0, succeeds, result.stdout + result.stderr)
        return result

    def test_updater_repins_both_asset_name_formats_without_other_source_changes(self):
        for old_name in ("T3-Code-0.1.9-x86_64.AppImage", "T3-Code-x86_64.AppImage"):
            for new_name in ("T3-Code-0.1.11-x86_64.AppImage", "T3-Code-x86_64.AppImage"):
                with self.subTest(old_name=old_name, new_name=new_name):
                    other_source = "      - type: file\n        path: startt3code\n"
                    self.manifest.write_text(other_source + self.source(old_name))
                    self.run_updater([self.asset(new_name)])
                    self.assertEqual(self.manifest.read_text(),
                                     other_source + self.source(new_name, self.tag, self.digest))
        self.assertEqual(self.output.read_text().splitlines(), [f"version={self.tag}"] * 4)

    def test_updater_rejects_ambiguous_missing_and_wrong_arch_assets(self):
        candidates = (
            [],
            [self.asset("T3-Code-arm64.AppImage")],
            [self.asset("T3-Code-0.1.11-aarch64.AppImage")],
            [self.asset("T3-Code-unrelated-x86_64.AppImage")],
            [self.asset("T3-Code-0.1.10-x86_64.AppImage")],
            [self.asset("T3-Code-x86_64.AppImage"), self.asset("T3-Code-0.1.11-x86_64.AppImage")],
        )
        for assets in candidates:
            with self.subTest(assets=assets):
                source = self.source("T3-Code-x86_64.AppImage")
                self.manifest.write_text(source)
                self.run_updater(assets, succeeds=False)
                self.assertEqual(self.manifest.read_text(), source)

    def test_updater_rejects_moving_and_foreign_download_urls(self):
        name = "T3-Code-x86_64.AppImage"
        for url in (
            f"https://github.com/pingdotgg/t3code/releases/latest/download/{name}",
            f"https://github.com/pingdotgg/t3code/releases/download/latest/{name}",
            f"https://example.invalid/releases/download/{self.tag}/{name}",
        ):
            with self.subTest(url=url):
                source = self.source(name)
                self.manifest.write_text(source)
                self.run_updater([self.asset(name, browser_download_url=url)], succeeds=False)
                self.assertEqual(self.manifest.read_text(), source)
        self.run_updater([self.asset(name)], succeeds=False, tag="latest")

    def test_updater_rejects_checksum_mismatch_before_repinning(self):
        source = self.source("T3-Code-x86_64.AppImage")
        self.manifest.write_text(source)
        self.run_updater([self.asset("T3-Code-x86_64.AppImage", digest="sha256:" + "0" * 64)],
                         succeeds=False)
        self.assertEqual(self.manifest.read_text(), source)

    def run_release_verifier(self, source, succeeds=True):
        runtime_sources = "".join(
            f"      - type: archive\n        url: {url}\n        sha256: {'b' * 64}\n"
            for url in (
                "https://nodejs.org/dist/v24.21.0/node-v24.21.0-linux-x64.tar.xz",
                "https://cdn.kernel.org/pub/software/scm/git/git-2.56.0.tar.xz",
                "https://cdn.openbsd.org/pub/OpenBSD/OpenSSH/portable/openssh-10.5p1.tar.gz",
            )
        )
        self.manifest.write_text(runtime_sources + source)
        (self.root / "t3code-x86_64.flatpak").write_bytes(b"synthetic Flatpak bundle\n")
        workflow = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
        script = next(step["run"] for step in workflow["jobs"]["build"]["steps"]
                      if step.get("name") == "Verify release inputs and bundle")
        result = subprocess.run(
            ["bash", "-e", "-o", "pipefail", "-c", script],
            cwd=self.root, env=self.env, capture_output=True, text=True,
        )
        self.assertEqual(result.returncode == 0, succeeds, result.stdout + result.stderr)
        return result

    def test_release_verifier_accepts_both_immutable_name_formats(self):
        for name in ("T3-Code-0.1.11-x86_64.AppImage", "T3-Code-x86_64.AppImage"):
            with self.subTest(name=name):
                result = self.run_release_verifier(self.source(name, self.tag, self.digest))
                self.assertIn(f"releases/download/{self.tag}/{name}", result.stdout)
                self.assertIn(f"sha256: {self.digest}", result.stdout)

    def test_release_verifier_rejects_moving_duplicate_missing_hash_and_wrong_arch(self):
        source = self.source("T3-Code-x86_64.AppImage", self.tag, self.digest)
        for invalid in (
            source.replace(f"download/{self.tag}", "latest/download"),
            source.replace(f"download/{self.tag}", "download/latest"),
            source + source,
            source.replace(f"        sha256: {self.digest}\n", ""),
            source.replace("T3-Code-x86_64.AppImage", "T3-Code-aarch64.AppImage"),
        ):
            with self.subTest(source=invalid):
                self.run_release_verifier(invalid, succeeds=False)


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
        (self.repo / "README.md").write_text("Original documentation\n")
        self.git("add", self.manifest.name)
        self.git("add", "README.md")
        self.git("commit", "-m", "initial")
        self.git("remote", "add", "origin", str(self.origin))
        self.git("push", "origin", "main")
        self.original_sha = self.git("rev-parse", "HEAD").strip()
        self.tag = "t3code-v1.2.3"
        self.output = self.root / "output"
        candidate_dir = self.root / "t3-update-candidate"
        candidate_dir.mkdir()
        self.candidate = candidate_dir / self.manifest.name
        self.state_file = self.root / "gh-state.json"
        self.write_state({"calls": [], "runs": []})
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        self.bin_dir = bin_dir
        stub = bin_dir / "gh"
        stub.write_text(GH_STUB)
        stub.chmod(0o755)
        self.env = {
            **os.environ,
            "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}",
            "VERSION": "v1.2.3",
            "GITHUB_OUTPUT": str(self.output),
            "GITHUB_ENV": str(self.root / "environment"),
            "GITHUB_WORKSPACE": str(self.repo),
            "GITHUB_REPOSITORY": "example/project",
            "GIT_CONFIG_GLOBAL": str(self.root / "gitconfig"),
            "GIT_CONFIG_NOSYSTEM": "1",
            "RUNNER_TEMP": str(self.root),
            "CUSTOM_TOKEN": "",
            "RELEASE_TAG": self.tag,
            "PUBLISH_FLATPAK_REPO": "true",
            "GH_TEST_STATE": str(self.state_file),
            "GH_TEST_RELEASE_SHA": self.original_sha,
            "GH_TEST_MAIN_SHA": self.original_sha,
            "VALIDATED_TREE": "",
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

    def prepare(self):
        self.output.write_text("")
        self.run_script(PREPARE)
        outputs = dict(line.split("=", 1) for line in self.output.read_text().splitlines())
        self.env.update({
            "BASE_SHA": outputs["base_sha"],
            "MANIFEST_SHA256": outputs["manifest_sha256"],
            "NEEDS_VALIDATION": outputs["needs_validation"],
        })
        if outputs["needs_validation"] == "true":
            self.candidate.write_bytes(self.manifest.read_bytes())
        return outputs

    def capture_candidate_tree(self):
        # Exercise the actual artifact/receipt scripts in separate clean checkouts.
        # Workflow-contract tests below separately require the full runtime gates.
        self.git("reset", "--hard", self.env["BASE_SHA"])
        self.output.write_text("")
        self.run_script(APPLY_CANDIDATE)
        outputs = dict(line.split("=", 1) for line in self.output.read_text().splitlines())
        self.env["VALIDATED_TREE"] = outputs["tree_sha"]
        self.run_script(VERIFY_CANDIDATE)
        self.git("reset", "--hard", self.env["BASE_SHA"])

    def publish(self):
        outputs = self.prepare()
        if outputs["needs_validation"] == "true":
            self.capture_candidate_tree()
        self.run_script(PUBLISH)
        self.env["GH_TEST_MAIN_SHA"] = self.git("rev-parse", "HEAD").strip()
        self.env["GH_TEST_RELEASE_SHA"] = self.git("rev-parse", f"{self.tag}^{{commit}}").strip()
        return outputs

    def assert_origin_unchanged(self):
        self.assertEqual(self.git("--git-dir", str(self.origin), "rev-parse", "refs/heads/main").strip(), self.original_sha)
        self.assertEqual(self.git("--git-dir", str(self.origin), "tag", "--list"), "")

    def test_new_update_publishes_main_and_tag_together(self):
        self.manifest.write_text("version: new\n")
        outputs = self.publish()
        sha = self.git("rev-parse", "HEAD").strip()
        self.assertNotEqual(sha, self.original_sha)
        self.assertEqual(self.git("--git-dir", str(self.origin), "rev-parse", "refs/heads/main").strip(), sha)
        self.assertEqual(self.git("--git-dir", str(self.origin), "rev-parse", f"refs/tags/{self.tag}^{{commit}}").strip(), sha)
        self.assertEqual(outputs["tag"], self.tag)
        self.assertEqual(self.git("rev-parse", "HEAD^{tree}").strip(), self.env["VALIDATED_TREE"])
        self.assertIn("Assisted by: ChatGPT", self.git("log", "-1", "--format=%B"))

    def test_rejected_tag_does_not_advance_main(self):
        hook = self.origin / "hooks/pre-receive"
        hook.write_text('#!/bin/sh\nwhile read old new ref; do\n  case "$ref" in refs/tags/*) exit 1 ;; esac\ndone\n')
        hook.chmod(0o755)
        self.manifest.write_text("version: new\n")
        self.prepare()
        self.capture_candidate_tree()
        self.run_script(PUBLISH, succeeds=False)
        self.assert_origin_unchanged()

    def test_missing_tag_and_existing_tag_both_reach_dispatch(self):
        self.assertNotIn("if", DISPATCH_STEP)
        first = self.publish()
        self.assertEqual(first["needs_validation"], "true")
        released_sha = self.git("rev-parse", "HEAD").strip()
        self.assertNotEqual(released_sha, self.original_sha)
        self.assertEqual(self.git("rev-parse", "HEAD^{tree}").strip(),
                         self.git("rev-parse", f"{self.original_sha}^{{tree}}").strip())
        second = self.publish()
        self.assertEqual(second["needs_validation"], "false")
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), released_sha)
        self.run_script(DISPATCH)
        self.assertEqual(len(self.dispatched()), 4)

    def test_existing_release_skips_artifact_and_validation_without_new_commit(self):
        self.create_tag()
        outputs = self.prepare()
        self.assertEqual(outputs["needs_validation"], "false")
        self.assertFalse(self.candidate.exists())
        self.run_script(PUBLISH)
        self.assertEqual(self.git("rev-parse", "HEAD").strip(), self.original_sha)
        self.run_script(DISPATCH)
        self.assertEqual(len(self.dispatched()), 4)

    def test_new_inputs_cannot_replace_an_existing_release_tag(self):
        self.create_tag()
        self.manifest.write_text("version: changed\n")
        self.run_script(PREPARE, succeeds=False)
        self.assertEqual(self.git("--git-dir", str(self.origin), "rev-parse", "refs/heads/main").strip(), self.original_sha)
        self.assertEqual(self.git("--git-dir", str(self.origin), "rev-parse", f"refs/tags/{self.tag}^{{commit}}").strip(), self.original_sha)

    def test_missing_validation_receipt_cannot_publish(self):
        self.manifest.write_text("version: new\n")
        self.prepare()
        self.run_script(PUBLISH, succeeds=False)
        self.assert_origin_unchanged()

    def test_tampered_candidate_artifact_cannot_be_applied_or_published(self):
        self.manifest.write_text("version: new\n")
        self.prepare()
        self.capture_candidate_tree()
        self.candidate.write_text("version: different\n")
        self.run_script(APPLY_CANDIDATE, succeeds=False)
        self.run_script(PUBLISH, succeeds=False)
        self.assert_origin_unchanged()

    def test_publisher_cannot_include_untested_source_changes(self):
        self.manifest.write_text("version: new\n")
        self.prepare()
        self.capture_candidate_tree()
        (self.repo / "README.md").write_text("Untested source changes\n")
        self.git("add", "README.md")
        self.run_script(PUBLISH, succeeds=False)
        self.assert_origin_unchanged()

    def test_validation_receipt_detects_staged_and_unstaged_changes(self):
        self.manifest.write_text("version: new\n")
        self.prepare()
        self.capture_candidate_tree()
        for staged in (False, True):
            with self.subTest(staged=staged):
                self.git("reset", "--hard", self.original_sha)
                self.run_script(APPLY_CANDIDATE)
                (self.repo / "README.md").write_text("Changed during validation\n")
                if staged:
                    self.git("add", "README.md")
                self.run_script(VERIFY_CANDIDATE, succeeds=False)
        self.assert_origin_unchanged()

    def concurrent_commit(self):
        self.git("checkout", "-b", "concurrent", self.original_sha)
        (self.repo / "README.md").write_text("Concurrent main update\n")
        self.git("add", "README.md")
        self.git("commit", "-m", "concurrent update")
        sha = self.git("rev-parse", "HEAD").strip()
        self.git("checkout", "main")
        return sha

    def test_concurrent_main_change_leaves_new_main_and_tags_untouched(self):
        self.manifest.write_text("version: new\n")
        self.prepare()
        self.capture_candidate_tree()
        concurrent_sha = self.concurrent_commit()
        self.git("push", "origin", f"{concurrent_sha}:refs/heads/main")
        result = self.run_script(PUBLISH, succeeds=False)
        self.assertIn("main changed", result.stderr)
        self.assertEqual(self.git("--git-dir", str(self.origin), "rev-parse", "refs/heads/main").strip(), concurrent_sha)
        self.assertEqual(self.git("--git-dir", str(self.origin), "tag", "--list"), "")

    def test_main_race_after_preflight_rejects_both_refs_atomically(self):
        # Even an unchanged manifest/missing tag needs a real main update, so
        # the remote compares the old SHA while atomically creating the tag.
        self.prepare()
        self.capture_candidate_tree()
        concurrent_sha = self.concurrent_commit()
        real_git = shutil.which("git")
        wrapper = self.bin_dir / "git"
        wrapper.write_text(
            f"#!{sys.executable}\n"
            "import os, subprocess, sys\n"
            f"real_git = {real_git!r}\n"
            "args = sys.argv[1:]\n"
            "if args[:1] == ['push'] and '--atomic' in args:\n"
            f"    subprocess.run([real_git, 'push', 'origin', {concurrent_sha + ':refs/heads/main'!r}], check=True)\n"
            "os.execv(real_git, [real_git, *args])\n"
        )
        wrapper.chmod(0o755)
        self.run_script(PUBLISH, succeeds=False)
        self.assertEqual(self.git("--git-dir", str(self.origin), "rev-parse", "refs/heads/main").strip(), concurrent_sha)
        self.assertEqual(self.git("--git-dir", str(self.origin), "tag", "--list"), "")

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
        self.publish()  # Retry now finds an existing release tag and skips validation.
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


class PublicationWorkflowContractTests(unittest.TestCase):
    def assert_gate_order(self, job, names):
        actual = [step.get("name") for step in job["steps"]]
        positions = [actual.index(name) for name in names]
        self.assertEqual(positions, sorted(positions))
        for step in job["steps"]:
            self.assertFalse(step.get("continue-on-error", False))

    def test_hourly_update_is_main_only_and_separates_publication_permissions(self):
        triggers = WORKFLOW.get("on", WORKFLOW.get(True))
        cron = triggers["schedule"][0]["cron"].split()
        self.assertTrue(1 <= int(cron[0]) <= 59)
        self.assertEqual(cron[1:], ["*"] * 4)
        self.assertEqual(WORKFLOW["permissions"], {"contents": "read"})
        self.assertEqual(WORKFLOW["jobs"]["update"]["if"], "${{ github.ref == 'refs/heads/main' }}")
        for name in ("update", "validate"):
            job = WORKFLOW["jobs"][name]
            self.assertNotIn("secrets.", json.dumps(job))
            checkout = next(step for step in job["steps"] if step.get("uses", "").startswith("actions/checkout@"))
            self.assertFalse(checkout["with"]["persist-credentials"])
        publish = WORKFLOW["jobs"]["publish"]
        self.assertEqual(publish["permissions"], {"contents": "write", "actions": "write"})
        self.assertEqual(publish["needs"], ["update", "validate"])
        # Failed/cancelled validation never reaches a mutation. A genuine no-op
        # bypasses the expensive job while still repairing missing dispatches.
        self.assertEqual(publish["if"],
                         "${{ always() && !cancelled() && github.ref == 'refs/heads/main' && needs.update.result == 'success' && (needs.update.outputs.needs_validation == 'false' || needs.validate.result == 'success') }}")
        self.assertEqual(WORKFLOW["jobs"]["validate"]["if"], "needs.update.outputs.needs_validation == 'true'")

    def test_candidate_runs_source_build_and_all_installed_checks_before_receipt(self):
        validate = WORKFLOW["jobs"]["validate"]
        self.assert_gate_order(validate, [
            "Apply exact candidate inputs", "Check candidate source", "Build candidate bundle",
            "Check installed candidate", "Verify candidate stayed unchanged",
        ])
        checks = steps(validate)
        self.assertEqual(checks["Check candidate source"]["run"], "bash tests/check-publication.sh source")
        installed = checks["Check installed candidate"]
        self.assertEqual(installed["run"], "bash tests/check-publication.sh installed t3code-candidate.flatpak")
        self.assertEqual(installed["env"]["T3_DISPOSABLE_ACCOUNT"], "1")
        self.assertNotIn("if", installed)
        self.assertEqual(checks["Build candidate bundle"]["with"]["bundle"], "t3code-candidate.flatpak")
        self.assertFalse(checks["Build candidate bundle"]["with"]["upload-artifact"])

    def test_release_tests_the_uploaded_bundle_before_write_privileges(self):
        release = yaml.safe_load((ROOT / ".github/workflows/release.yml").read_text())
        self.assertEqual(release["permissions"], {"contents": "read"})
        build = release["jobs"]["build"]
        self.assert_gate_order(build, [
            "Check release source", "Build release bundle", "Check installed release bundle",
            "Verify release inputs and bundle", "Upload verified bundle",
        ])
        checks = steps(build)
        installed = checks["Check installed release bundle"]
        bundle = checks["Build release bundle"]["with"]["bundle"]
        self.assertEqual(installed["run"], f"bash tests/check-publication.sh installed {bundle}")
        self.assertEqual(installed["env"]["T3_DISPOSABLE_ACCOUNT"], "1")
        self.assertNotIn("if", installed)
        self.assertIn(bundle, checks["Upload verified bundle"]["with"]["path"].splitlines())
        self.assertNotIn("secrets.", json.dumps(build))
        self.assertEqual(release["jobs"]["release"]["needs"], "build")
        self.assertEqual(release["jobs"]["release"]["permissions"], {"contents": "write"})

    def test_pages_tests_exported_commit_before_archive_and_signing(self):
        pages = yaml.safe_load((ROOT / ".github/workflows/pages-flatpak-repo.yml").read_text())
        build = pages["jobs"]["build"]
        self.assert_gate_order(build, [
            "Check repository source", "Build unsigned architecture repository",
            "Check installed repository bundle", "Archive validated architecture repository",
            "Upload unsigned architecture repository",
        ])
        checks = steps(build)
        installed = checks["Check installed repository bundle"]
        self.assertEqual(installed["env"]["T3_DISPOSABLE_ACCOUNT"], "1")
        self.assertIn('"repo-${arch}" "$bundle" "$APP_ID" master', installed["run"])
        self.assertIn('bash tests/check-publication.sh installed "$bundle"', installed["run"])
        self.assertNotIn("if", installed)
        self.assertIn("t3code-commit-${{ matrix.variant.arch }}.txt", checks["Upload unsigned architecture repository"]["with"]["path"])
        self.assertNotIn("secrets.", json.dumps(build))
        aggregate = pages["jobs"]["aggregate"]
        self.assertEqual(aggregate["needs"], "build")
        sign = steps(aggregate)["Validate, sign, and aggregate exact application ref"]["run"]
        self.assertLess(sign.index('test "$(ostree rev-parse --repo="$source_repo" "$expected_ref")" = "$validated_commit"'), sign.index("flatpak build-sign"))
        self.assertLess(sign.index('test "$(ostree rev-parse --repo=site/repo "$expected_ref")" = "$validated_commit"'), sign.index("flatpak build-sign"))


class PublicationGateTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        tests = self.root / "tests"
        tests.mkdir()
        self.helper = tests / "check-publication.sh"
        shutil.copyfile(ROOT / "tests/check-publication.sh", self.helper)
        self.bundle = self.root / "test.flatpak"
        self.bundle.write_bytes(b"synthetic bundle for gate-routing tests\n")
        self.log = self.root / "calls.jsonl"
        self.bin = self.root / "bin"
        self.bin.mkdir()
        stub_source = (
            f"#!{sys.executable}\n"
            "import json, os, pathlib, sys\n"
            "args = [pathlib.Path(sys.argv[0]).name, *sys.argv[1:]]\n"
            "with open(os.environ['CHECK_CALLS'], 'a') as log:\n"
            "    log.write(json.dumps(args) + '\\n')\n"
            "if os.environ.get('CHECK_FAIL') and os.environ['CHECK_FAIL'] in ' '.join(args):\n"
            "    sys.exit(19)\n"
            "if os.environ.get('CHECK_MUTATE') and os.environ['CHECK_MUTATE'] in ' '.join(args):\n"
            "    pathlib.Path(args[-1]).write_bytes(b'changed after installation')\n"
        )
        for command in ("python3", "node", "bash"):
            stub = self.bin / command
            stub.write_text(stub_source)
            stub.chmod(0o755)

    def invoke(self, mode, failure="", disposable="1", mutation=""):
        result = subprocess.run(
            [shutil.which("bash"), str(self.helper), mode, *([str(self.bundle)] if mode == "installed" else [])],
            cwd=self.root,
            env={**os.environ, "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
                 "RUNNER_TEMP": str(self.root), "CHECK_CALLS": str(self.log),
                 "CHECK_FAIL": failure, "T3_DISPOSABLE_ACCOUNT": disposable, "CHECK_MUTATE": mutation},
            capture_output=True, text=True,
        )
        calls = [json.loads(line) for line in self.log.read_text().splitlines()] if self.log.exists() else []
        return result, calls

    def test_source_gates_include_every_python_suite_and_file_boundary_tests(self):
        result, calls = self.invoke("source")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(calls[0][:3], ["python3", "-m", "venv"])
        self.assertEqual(calls[1][:4], ["python3", "-m", "pip", "install"])
        self.assertIn(["python3", "-m", "unittest", "discover", "-s", "tests", "-p", "test_*.py"], calls)
        self.assertIn(["node", "--test", "tests/test-preview-files.mjs"], calls)
        self.assertIn(["python3", "scripts/check-flatpak-security.py"], calls)
        self.assertIn(["python3", "scripts/check-pages-artifact-contract.py"], calls)

    def test_source_failure_stops_later_gates(self):
        result, calls = self.invoke("source", failure="-m unittest")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 3)
        self.assertFalse(any(call[0] == "node" for call in calls))

    def test_installed_gates_use_one_bundle_and_stop_on_failure(self):
        result, calls = self.invoke("installed", failure="check-ssh-boundary.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual([Path(call[1]).name for call in calls], ["with-flatpak-portal.sh", "check-ssh-boundary.sh"])
        self.assertEqual(calls[-1][-1], str(self.bundle))
        self.log.unlink()
        result, calls = self.invoke("installed")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([Path(call[1]).name for call in calls], [
            "with-flatpak-portal.sh", "check-ssh-boundary.sh", "run-tool-extension-check.sh",
        ])
        self.assertTrue(all(call[-1] == str(self.bundle) for call in calls[1:]))

    def test_installed_gates_require_disposable_account(self):
        result, calls = self.invoke("installed", disposable="")
        self.assertEqual(result.returncode, 2)
        self.assertEqual(calls, [])

    def test_bundle_changed_during_installed_checks_is_rejected(self):
        result, calls = self.invoke("installed", mutation="run-tool-extension-check.sh")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(len(calls), 3)


if __name__ == "__main__":
    unittest.main()
