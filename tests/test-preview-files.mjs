import assert from "node:assert/strict";
import { constants } from "node:fs";
import fs from "node:fs/promises";
import { EventEmitter } from "node:events";
import os from "node:os";
import path from "node:path";
import test from "node:test";
import { isolatedPlaywright, saveDownload, stageUploads } from "../preview/adapter.mjs";

test("transfers preserve authorized content and refuse browser-controlled read paths", async () => {
  const scratch = path.join(os.homedir(), "agent-scratch/t3-flatpak/preview-unit-tests");
  await fs.mkdir(scratch, { recursive: true });
  const root = await fs.mkdtemp(path.join(scratch, "files-"));
  const previous = process.env.TMPDIR;
  process.env.TMPDIR = root;
  const context = new EventEmitter();
  const page = new EventEmitter();
  page.context = () => context;
  let staging;
  const chromium = { launchPersistentContext: async (_, options) => {
    staging = options.downloadsPath;
    return context;
  } };
  try {
    await isolatedPlaywright({ chromium }).chromium.launchPersistentContext("profile");
    const source = path.join(root, "source.txt");
    await fs.writeFile(source, "authorized upload");
    const alias = path.join(root, "alias.txt");
    await fs.symlink(source, alias);
    const [uploaded] = await stageUploads(page, [alias]);
    assert.equal(path.basename(uploaded), "alias.txt");
    assert.equal(await fs.readFile(uploaded, "utf8"), "authorized upload");
    const tree = path.join(root, "directory");
    await fs.mkdir(tree);
    await fs.writeFile(path.join(tree, "nested.txt"), "directory upload");
    const [directory] = await stageUploads(page, [tree]);
    assert.equal(await fs.readFile(path.join(directory, "nested.txt"), "utf8"), "directory upload");
    await fs.symlink(tree, path.join(tree, "cycle"));
    await assert.rejects(stageUploads(page, [tree]), /symlink cycle/);

    const download = (filename) => ({ page: () => page, path: async () => filename });
    const artifact = path.join(staging, "artifact");
    const destination = path.join(root, "saved");
    await fs.writeFile(artifact, "download bytes");
    await saveDownload(download(artifact), destination);
    assert.equal(await fs.readFile(destination, "utf8"), "download bytes");
    assert.equal((await fs.stat(destination)).mode & 0o777, 0o600);
    await assert.rejects(saveDownload(download(source), path.join(root, "escape")), /escaped/);
    await fs.unlink(artifact);
    await fs.symlink(source, artifact);
    await assert.rejects(saveDownload(download(artifact), path.join(root, "symlink")), { code: "ELOOP" });
    assert.equal(await fs.readFile(source, "utf8"), "authorized upload");
    await fs.unlink(artifact);
    await fs.mkdir(artifact);
    await assert.rejects(saveDownload(download(artifact), path.join(root, "directory-download")), /regular file/);
  } finally {
    if (previous === undefined) delete process.env.TMPDIR;
    else process.env.TMPDIR = previous;
    context.emit("close");
    page.emit("close");
    await fs.rm(root, { recursive: true, force: true });
  }
});
